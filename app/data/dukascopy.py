from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from hashlib import sha256
import json
import logging
import math
from pathlib import Path
import time
from typing import Any, Iterable

import httpx
import numpy as np
import pandas as pd

from .base import DataProviderError, empty_canonical_frame, normalize_canonical, utc_timestamp
from .cache import PartitionedParquetStore
from .resampler import resample_market_data
from .validation import validate_market_data


LOGGER = logging.getLogger(__name__)

# Verified against the instrument metadata used by Dukascopy's official
# Historical Data Export widget on 2026-08-25.
DUKASCOPY_SYMBOLS = {
    "EUR_USD": "EUR-USD",
    "GBP_USD": "GBP-USD",
    "USD_JPY": "USD-JPY",
    "USD_CAD": "USD-CAD",
    "USD_SEK": "USD-SEK",
    "USD_CHF": "USD-CHF",
    "DXY": "DOLLAR.IDX-USD",
}


class DukascopyError(DataProviderError):
    pass


def _js_round(values: np.ndarray, precision: float = 1.0) -> np.ndarray:
    """Match JavaScript Math.round for the non-negative Dukascopy fields."""

    return np.floor(values * precision + 0.5) / precision


def decode_candle_payload(payload: dict[str, Any]) -> pd.DataFrame:
    """Decode Dukascopy Jetta's delta-compressed candle JSON."""

    arrays = [payload.get(name, []) for name in ("times", "opens", "highs", "lows", "closes", "volumes")]
    if not arrays[0]:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"],
            index=pd.DatetimeIndex([], tz="UTC", name="timestamp"),
        )
    length = len(arrays[0])
    if any(len(values) != length for values in arrays):
        raise DukascopyError("Dukascopy OHLCV payload arrays have inconsistent lengths")

    shift = int(payload.get("shift") or 1)
    multiplier = float(payload.get("multiplier") or 1)
    if multiplier <= 0:
        raise DukascopyError("Dukascopy payload multiplier must be positive")
    exponent = math.floor(math.log10(multiplier))
    precision = multiplier if exponent > 0 else 10 ** abs(exponent)

    timestamp = int(payload.get("timestamp") or 0) + np.cumsum(np.asarray(arrays[0], dtype=np.int64)) * shift
    decoded: dict[str, np.ndarray] = {}
    for name, base, deltas in zip(
        ("open", "high", "low", "close"),
        (payload.get("open", 0), payload.get("high", 0), payload.get("low", 0), payload.get("close", 0)),
        arrays[1:5],
    ):
        values = float(base) + np.cumsum(np.asarray(deltas, dtype=float)) * multiplier
        decoded[name] = _js_round(values, precision)
    decoded["volume"] = np.floor(np.asarray(arrays[5], dtype=float) * 1_000_000 + 0.5)

    index = pd.to_datetime(timestamp, unit="ms", utc=True)
    frame = pd.DataFrame(decoded, index=pd.DatetimeIndex(index, name="timestamp"))
    return frame.sort_index(kind="mergesort").loc[lambda x: ~x.index.duplicated(keep="last")]


def combine_bid_ask(bid: pd.DataFrame, ask: pd.DataFrame) -> pd.DataFrame:
    """Combine real BID and ASK M1 candles without filling unmatched minutes."""

    if bid.empty or ask.empty:
        return empty_canonical_frame()
    left = bid.rename(columns={column: f"bid_{column[0]}" for column in ("open", "high", "low", "close")})
    left = left.rename(columns={"volume": "bid_volume"})
    right = ask.rename(columns={column: f"ask_{column[0]}" for column in ("open", "high", "low", "close")})
    right = right.rename(columns={"volume": "ask_volume"})
    frame = left.join(right, how="inner")
    for suffix in "ohlc":
        frame[f"mid_{suffix}"] = (frame[f"bid_{suffix}"] + frame[f"ask_{suffix}"]) / 2.0
    # Compatibility volume: the official exporter's default BID-side volume.
    # Both independent side measures remain available in the canonical cache.
    frame["volume"] = frame["bid_volume"]
    return normalize_canonical(frame)


class DukascopyClient:
    """No-key client for the API used by Dukascopy's official export widget."""

    def __init__(
        self,
        base_url: str = "https://jetta.dukascopy.com/v1",
        timeout_seconds: float = 30.0,
        retries: int = 4,
        backoff_seconds: float = 0.5,
    ):
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout_seconds),
            headers={"User-Agent": "RiseUp-Backtester/1.0 historical-research"},
        )
        self.retries = max(1, retries)
        self.backoff_seconds = max(0.0, backoff_seconds)

    def close(self) -> None:
        self.client.close()

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                response = self.client.get(path, params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise DukascopyError(f"Unexpected Dukascopy response at {path}")
                return payload
            except (httpx.HTTPError, ValueError, DukascopyError) as exc:
                last_error = exc
                if attempt + 1 < self.retries:
                    time.sleep(self.backoff_seconds * (2**attempt))
        raise DukascopyError(f"Dukascopy request failed after {self.retries} attempts: {path}: {last_error}")

    def instrument(self, provider_symbol: str) -> dict[str, Any]:
        return self._get_json(f"/instruments/{provider_symbol}")

    def fetch_day(self, provider_symbol: str, day: date, side: str, now: pd.Timestamp | None = None) -> pd.DataFrame:
        side = side.upper()
        if side not in {"BID", "ASK"}:
            raise ValueError("side must be BID or ASK")
        day_start = pd.Timestamp(day, tz="UTC")
        current = utc_timestamp(now or datetime.now(timezone.utc))
        if day_start.normalize() >= current.normalize():
            path = f"/candles/minute/{provider_symbol}/{side}"
            payload = self._get_json(path, params={"from": int(day_start.timestamp() * 1000)})
        else:
            path = f"/candles/minute/{provider_symbol}/{side}/{day.year}/{day.month}/{day.day}"
            payload = self._get_json(path)
        frame = decode_candle_payload(payload)
        if frame.empty:
            # Keep an explicit UTC DatetimeIndex even when Dukascopy returns
            # no candles (weekends, holidays, or empty provider responses).
            if not isinstance(frame.index, pd.DatetimeIndex):
                frame.index = pd.DatetimeIndex([], tz="UTC", name="timestamp")
            elif frame.index.tz is None:
                frame.index = frame.index.tz_localize("UTC")
            else:
                frame.index = frame.index.tz_convert("UTC")
            return frame

        day_end = day_start + pd.Timedelta(days=1)
        last_complete = current.floor("min")
        return frame.loc[(frame.index >= day_start) & (frame.index < min(day_end, last_complete))].copy()


class DukascopyProvider:
    name = "dukascopy"

    def __init__(
        self,
        data_dir: Path,
        validation_mode: str = "strict",
        anchor_timezone: str = "Europe/Paris",
        client: DukascopyClient | Any | None = None,
        workers: int = 4,
    ):
        self.store = PartitionedParquetStore(Path(data_dir) / "dukascopy")
        self.validation_mode = validation_mode
        self.anchor_timezone = anchor_timezone
        self.client = client
        self.workers = max(1, workers)

    @staticmethod
    def provider_symbol(instrument: str) -> str:
        try:
            return DUKASCOPY_SYMBOLS[instrument]
        except KeyError as exc:
            raise DukascopyError(f"Unsupported Dukascopy instrument: {instrument}") from exc

    def _network_client(self):
        if self.client is None:
            self.client = DukascopyClient()
        return self.client

    def _metadata(self, instrument: str, download: bool = False) -> dict[str, Any] | None:
        cached = self.store.load_metadata(instrument)
        if cached is not None or not download:
            return cached
        metadata = self._network_client().instrument(self.provider_symbol(instrument))
        self.store.save_metadata(instrument, metadata)
        return metadata

    def _fetch_day(self, instrument: str, day: date, now: pd.Timestamp) -> pd.DataFrame:
        symbol = self.provider_symbol(instrument)
        client = self._network_client()
        with ThreadPoolExecutor(max_workers=2) as executor:
            bid_future = executor.submit(client.fetch_day, symbol, day, "BID", now)
            ask_future = executor.submit(client.fetch_day, symbol, day, "ASK", now)
            bid = bid_future.result()
            ask = ask_future.result()
        if bid.empty and ask.empty:
            return empty_canonical_frame()
        if bid.empty != ask.empty:
            raise DukascopyError(f"{instrument} {day}: only one of BID/ASK was returned")
        return combine_bid_ask(bid, ask)

    @staticmethod
    def _days(start: pd.Timestamp, end: pd.Timestamp) -> list[date]:
        if start >= end:
            return []
        last = (end - pd.Timedelta(nanoseconds=1)).normalize()
        return [value.date() for value in pd.date_range(start.normalize(), last, freq="1D")]

    def download(
        self,
        instruments: Iterable[str],
        start,
        end,
        force: bool = False,
        validate: bool = False,
        now: pd.Timestamp | None = None,
    ) -> dict[str, dict[str, int]]:
        requested_start = utc_timestamp(start)
        requested_end = utc_timestamp(end)
        current = utc_timestamp(now or datetime.now(timezone.utc))
        effective_end = min(requested_end, current.floor("min"))
        result: dict[str, dict[str, int]] = {}

        for instrument in instruments:
            symbol = self.provider_symbol(instrument)
            metadata = self._metadata(instrument, download=True) or {}
            holidays = metadata.get("holidays", [])
            completed = self.store.completed_days(instrument)
            all_days = self._days(requested_start, effective_end)
            planned = [
                day
                for day in all_days
                if force or day.isoformat() not in completed or pd.Timestamp(day, tz="UTC") == current.normalize()
            ]
            stats = {"downloaded_days": 0, "cached_days": len(all_days) - len(planned), "rows": 0, "unexpected_gaps": 0}
            result[instrument] = stats
            if not planned:
                LOGGER.info("[%s] cache present for requested period", instrument)
                continue

            by_month: dict[str, list[date]] = {}
            for day in planned:
                by_month.setdefault(day.strftime("%Y-%m"), []).append(day)

            for month, month_days in sorted(by_month.items()):
                LOGGER.info("[%s] downloading %s (%d day(s))", instrument, month, len(month_days))
                month_rows = 0
                month_gaps = 0
                with ThreadPoolExecutor(max_workers=self.workers) as executor:
                    futures = {executor.submit(self._fetch_day, instrument, day, current): day for day in month_days}
                    for future in as_completed(futures):
                        day = futures[future]
                        frame = future.result()
                        day_start = pd.Timestamp(day, tz="UTC")
                        day_end = min(day_start + pd.Timedelta(days=1), effective_end)
                        if validate:
                            report = validate_market_data(frame, instrument, day_start, day_end, holidays)
                            if report.invalid_ohlc_rows or report.invalid_value_rows:
                                report.raise_for_errors(instrument)
                            stats["unexpected_gaps"] += report.unexpected_missing_minutes
                            month_gaps += report.unexpected_missing_minutes
                        complete = day_start + pd.Timedelta(days=1) <= current.floor("min")
                        self.store.write_day(instrument, day, frame, symbol, complete, current)
                        stats["downloaded_days"] += 1
                        stats["rows"] += len(frame)
                        month_rows += len(frame)
                LOGGER.info(
                    "[%s] %s OK - %d candle(s), %d unexpected missing minute(s)",
                    instrument,
                    month,
                    month_rows,
                    month_gaps,
                )
        return result

    def get(self, instrument: str, start, end, interval: str = "1min") -> pd.DataFrame:
        start_ts = utc_timestamp(start)
        end_ts = utc_timestamp(end)
        frame = self.store.read(instrument, start_ts, end_ts)
        metadata = self._metadata(instrument, download=False) or {}
        report = validate_market_data(frame, instrument, start_ts, end_ts, metadata.get("holidays", []))
        if self.validation_mode == "strict":
            report.raise_for_errors(instrument)
<<<<<<< HEAD
        elif self.validation_mode == "trace" and any((report.invalid_ohlc_rows, report.invalid_value_rows, report.duplicate_timestamps, report.out_of_order_timestamps)):
            raise ValueError(f"TRACE: invalid OHLC/values/order for {instrument}; repair required")
        elif not report.is_valid:
            first_gap = report.unexpected_gaps[0] if report.unexpected_gaps else None
            LOGGER.warning(
                "[%s] non-strict validation: rows=%d invalid_ohlc=%d invalid_values=%d "
=======
        elif not report.is_valid:
            first_gap = report.unexpected_gaps[0] if report.unexpected_gaps else None
            LOGGER.warning(
                "[%s] permissive validation: rows=%d invalid_ohlc=%d invalid_values=%d "
>>>>>>> 853804b008cb85b1a2c913966f2c28e9a257535a
                "duplicate_timestamps=%d out_of_order=%d missing_minutes=%d gap_events=%d%s",
                instrument,
                report.rows,
                report.invalid_ohlc_rows,
                report.invalid_value_rows,
                report.duplicate_timestamps,
                report.out_of_order_timestamps,
                report.unexpected_missing_minutes,
                len(report.unexpected_gaps),
                (
                    f" first_gap={first_gap.start}..{first_gap.end}"
                    if first_gap is not None
                    else ""
                ),
            )

        if interval in {"1min", "M1"}:
            return frame
        return resample_market_data(frame, interval, self.anchor_timezone)

    def data_revision(self, instruments: Iterable[str], start, end) -> str:
        """Hash the exact monthly cache partitions relevant to a request."""

        start_ts, end_ts = utc_timestamp(start), utc_timestamp(end)
        first = start_ts.tz_localize(None).to_period("M")
        last = (end_ts - pd.Timedelta(nanoseconds=1)).tz_localize(None).to_period("M")
        months = {str(value) for value in pd.period_range(first, last, freq="M")}
        payload: dict[str, Any] = {
            "provider": self.name,
            "start": start_ts.isoformat(),
            "end": end_ts.isoformat(),
            "instruments": {},
        }
        for instrument in sorted(set(instruments)):
            manifest = self.store.load_manifest(instrument)
            payload["instruments"][instrument] = {
                month: details.get("sha256")
                for month, details in sorted(manifest.get("partitions", {}).items())
                if month in months
            }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode("utf-8")).hexdigest()

    def status(self) -> list[dict[str, Any]]:
        output = []
        for instrument in self.store.instruments():
            manifest = self.store.load_manifest(instrument)
            start = manifest.get("start")
            end = manifest.get("end")
            if start and end:
                end_exclusive = utc_timestamp(end) + pd.Timedelta(minutes=1)
                frame = self.store.read(instrument, start, end_exclusive)
                metadata = self._metadata(instrument, download=False) or {}
                report = validate_market_data(frame, instrument, start, end_exclusive, metadata.get("holidays", []))
            else:
                report = validate_market_data(empty_canonical_frame(), instrument)
            gaps_by_day: dict[str, int] = {}
            gaps_by_month: dict[str, int] = {}
            for gap in report.unexpected_gaps:
                day_key = gap.start[:10]
                month_key = gap.start[:7]
                gaps_by_day[day_key] = gaps_by_day.get(day_key, 0) + gap.missing_minutes
                gaps_by_month[month_key] = gaps_by_month.get(month_key, 0) + gap.missing_minutes
            output.append(
                {
                    "symbol": instrument,
                    "provider_symbol": manifest.get("provider_symbol") or self.provider_symbol(instrument),
                    "start": start,
                    "end": end,
                    "rows": int(manifest.get("rows", 0)),
                    "unexpected_gaps": report.unexpected_missing_minutes,
                    "gap_events": len(report.unexpected_gaps),
                    "largest_gap_minutes": max(
                        (gap.missing_minutes for gap in report.unexpected_gaps), default=0
                    ),
                    "gaps_by_day": gaps_by_day,
                    "gaps_by_month": gaps_by_month,
                    "classifications": {
                        "UNEXPECTED_DATA_GAP": report.unexpected_missing_minutes,
                        "MARKET_CLOSED": report.expected_market_closed_minutes,
                        "HOLIDAY": report.expected_holiday_minutes,
                        "EXPECTED_PROVIDER_GAP": report.expected_provider_gap_minutes,
                    },
                    "completed_days": len(manifest.get("completed_days", [])),
                    "partial_days": len(manifest.get("partial_days", [])),
                    "last_download": manifest.get("generated_at"),
                }
            )
        return output
