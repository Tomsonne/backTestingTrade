from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

import numpy as np
import pandas as pd

from .base import DataProviderError, utc_timestamp
from .dukascopy import DUKASCOPY_SYMBOLS, DukascopyProvider, combine_bid_ask


class DukascopyCsvError(DataProviderError):
    pass


def detect_symbol(path: Path) -> str | None:
    normalized = re.sub(r"[^A-Z0-9]+", "-", path.name.upper())
    for internal, provider in sorted(DUKASCOPY_SYMBOLS.items(), key=lambda item: -len(item[1])):
        if provider in normalized or internal.replace("_", "-") in normalized:
            return internal
    return None


def detect_side(path: Path) -> str | None:
    upper = path.name.upper()
    if re.search(r"(^|[^A-Z])BID([^A-Z]|$)", upper):
        return "BID"
    if re.search(r"(^|[^A-Z])ASK([^A-Z]|$)", upper):
        return "ASK"
    return None


def _column(columns: list[str], candidates: tuple[str, ...]) -> str | None:
    lookup = {re.sub(r"[^a-z0-9]", "", value.lower()): value for value in columns}
    for candidate in candidates:
        found = lookup.get(re.sub(r"[^a-z0-9]", "", candidate.lower()))
        if found:
            return found
    return None


def read_dukascopy_csv(
    path: Path,
    side: str,
    source_timezone: str | None = None,
) -> pd.DataFrame:
    """Parse an official Dukascopy single-side OHLCV CSV export."""

    frame = pd.read_csv(path)
    if frame.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    columns = list(frame.columns)
    timestamp_column = _column(columns, ("timestamp", "time", "datetime", "gmt time")) or columns[0]
    mapping = {
        name: _column(columns, (name,)) for name in ("open", "high", "low", "close", "volume")
    }
    if any(mapping[name] is None for name in ("open", "high", "low", "close")):
        raise DukascopyCsvError("CSV must contain timestamp, Open, High, Low and Close columns")

    raw_time = frame[timestamp_column]
    parsed = pd.to_datetime(raw_time, errors="raise", format="mixed")
    if parsed.dt.tz is None:
        timezone_name = source_timezone or timestamp_column or "UTC"
        if timezone_name.upper() in {"GMT", "UTC", "TIME"}:
            timezone_name = "UTC"
        try:
            parsed = parsed.dt.tz_localize(timezone_name, ambiguous="infer", nonexistent="raise")
        except (TypeError, ValueError) as exc:
            raise DukascopyCsvError(
                f"Naive CSV timestamps require a valid source timezone; got {timezone_name!r}"
            ) from exc
    parsed = parsed.dt.tz_convert("UTC")

    output = pd.DataFrame(index=pd.DatetimeIndex(parsed, name="timestamp"))
    for name in ("open", "high", "low", "close"):
        output[name] = pd.to_numeric(frame[mapping[name]], errors="coerce").to_numpy()
    if mapping["volume"] is None:
        output["volume"] = np.nan
    else:
        output["volume"] = pd.to_numeric(frame[mapping["volume"]], errors="coerce").to_numpy()

    invalid = (
        output[["open", "high", "low", "close"]].isna().any(axis=1)
        | (output[["open", "high", "low", "close"]] <= 0).any(axis=1)
        | (output.high < output.open)
        | (output.high < output.close)
        | (output.low > output.open)
        | (output.low > output.close)
        | (output.high < output.low)
        | (output.volume < 0).fillna(False)
    )
    if invalid.any():
        raise DukascopyCsvError(f"CSV contains {int(invalid.sum())} invalid OHLCV row(s)")
    output = output.sort_index(kind="mergesort")
    return output.loc[~output.index.duplicated(keep="last")]


class DukascopyCsvImporter:
    """Import BID/ASK CSV exports into the downloader's canonical cache."""

    def __init__(self, provider: DukascopyProvider):
        self.provider = provider

    def import_file(
        self,
        path: Path,
        instrument: str | None = None,
        side: str | None = None,
        source_timezone: str | None = None,
        now: pd.Timestamp | None = None,
    ) -> dict:
        path = Path(path)
        instrument = instrument or detect_symbol(path)
        side = (side or detect_side(path) or "").upper()
        if not instrument:
            raise DukascopyCsvError("Could not detect symbol from filename; pass --symbol")
        if instrument not in DUKASCOPY_SYMBOLS:
            raise DukascopyCsvError(f"Unsupported instrument: {instrument}")
        if side not in {"BID", "ASK"}:
            raise DukascopyCsvError("Could not detect BID/ASK side from filename; pass --side")

        frame = read_dukascopy_csv(path, side, source_timezone)
        months = self.provider.store.write_import_side(instrument, side, frame)
        current = utc_timestamp(now or datetime.now(timezone.utc))
        materialized_rows = 0
        pending_months = []

        for month in months:
            bid = self.provider.store.read_import_side(instrument, "BID", month)
            ask = self.provider.store.read_import_side(instrument, "ASK", month)
            if bid.empty or ask.empty:
                pending_months.append(month)
                continue
            combined = combine_bid_ask(bid, ask)
            for day_key, day_frame in combined.groupby(combined.index.date):
                day_start = pd.Timestamp(day_key, tz="UTC")
                complete = day_start + pd.Timedelta(days=1) <= current.floor("min")
                self.provider.store.write_day(
                    instrument,
                    day_key,
                    day_frame,
                    self.provider.provider_symbol(instrument),
                    complete,
                    current,
                )
                materialized_rows += len(day_frame)

        return {
            "instrument": instrument,
            "side": side,
            "source_rows": len(frame),
            "materialized_rows": materialized_rows,
            "pending_counterpart_months": pending_months,
        }
