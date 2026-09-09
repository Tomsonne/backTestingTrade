from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Iterable

import numpy as np
import pandas as pd

from .base import DataCoverageError, utc_timestamp


PRICE_PREFIXES = ("bid", "ask", "mid")


@dataclass(frozen=True)
class Gap:
    start: str
    end: str
    missing_minutes: int
    classification: str = "UNEXPECTED_DATA_GAP"


@dataclass
class ValidationReport:
    rows: int
    duplicate_timestamps: int = 0
    out_of_order_timestamps: int = 0
    invalid_ohlc_rows: int = 0
    invalid_value_rows: int = 0
    unexpected_gaps: list[Gap] = field(default_factory=list)
    expected_market_closed_minutes: int = 0
    expected_holiday_minutes: int = 0
    expected_provider_gap_minutes: int = 0

    @property
    def unexpected_missing_minutes(self) -> int:
        return sum(gap.missing_minutes for gap in self.unexpected_gaps)

    @property
    def is_valid(self) -> bool:
        return not any(
            (
                self.duplicate_timestamps,
                self.out_of_order_timestamps,
                self.invalid_ohlc_rows,
                self.invalid_value_rows,
                self.unexpected_missing_minutes,
            )
        )

    def to_dict(self) -> dict:
        result = asdict(self)
        result["is_valid"] = self.is_valid
        result["unexpected_missing_minutes"] = self.unexpected_missing_minutes
        return result

    def raise_for_errors(self, instrument: str) -> None:
        if self.is_valid:
            return
        details = []
        if self.duplicate_timestamps:
            details.append(f"{self.duplicate_timestamps} duplicate timestamp(s)")
        if self.out_of_order_timestamps:
            details.append(f"{self.out_of_order_timestamps} out-of-order timestamp(s)")
        if self.invalid_ohlc_rows:
            details.append(f"{self.invalid_ohlc_rows} invalid OHLC row(s)")
        if self.invalid_value_rows:
            details.append(f"{self.invalid_value_rows} invalid value row(s)")
        if self.unexpected_missing_minutes:
            first = self.unexpected_gaps[0]
            details.append(
                f"{self.unexpected_missing_minutes} missing M1 candle(s), first gap "
                f"{first.start} to {first.end}"
            )
        raise DataCoverageError(
            f"Backtest aborted: {instrument} " + "; ".join(details) + ". "
            "Run historical data repair first."
        )


def _holiday_mask(index: pd.DatetimeIndex, holidays: Iterable[dict] | None) -> np.ndarray:
    closed = np.zeros(len(index), dtype=bool)
    if not holidays:
        return closed
    nanos = index.asi8
    for holiday in holidays:
        start = int(holiday.get("from", 0)) * 1_000_000
        end = int(holiday.get("till", 0)) * 1_000_000
        if start and end:
            closed |= (nanos >= start) & (nanos < end)
    return closed


def expected_market_open_mask(
    index: pd.DatetimeIndex,
    instrument: str,
    holidays: Iterable[dict] | None = None,
) -> np.ndarray:
    """Return Dukascopy schedule-aware market-open flags.

    FX follows the verified continuous Sunday 17:00 to Friday 17:00 New York
    schedule. Dukascopy's direct Dollar Index has a verified daily 17:00-20:00
    New York maintenance break. Instrument holiday intervals from the official
    metadata can additionally mark closures.
    """

    if index.tz is None:
        raise ValueError("Market timestamps must be timezone-aware")
    local = index.tz_convert("America/New_York")
    weekday = local.weekday.to_numpy()
    minute = (local.hour * 60 + local.minute).to_numpy()

    if instrument == "DXY":
        open_mask = (
            ((weekday == 6) & (minute >= 20 * 60))
            | ((weekday >= 0) & (weekday <= 3) & ((minute < 17 * 60) | (minute >= 20 * 60)))
            | ((weekday == 4) & (minute < 17 * 60))
        )
    else:
        open_mask = (
            ((weekday == 6) & (minute >= 17 * 60))
            | ((weekday >= 0) & (weekday <= 3))
            | ((weekday == 4) & (minute < 17 * 60))
        )

    return open_mask & ~_holiday_mask(index, holidays)


def _expected_minute_classes(
    index: pd.DatetimeIndex,
    instrument: str,
    holidays: Iterable[dict] | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Split expected minutes into open, scheduled-closed and holiday classes."""

    schedule_open = expected_market_open_mask(index, instrument, None)
    holiday = schedule_open & _holiday_mask(index, holidays)
    market_closed = ~schedule_open
    return schedule_open & ~holiday, market_closed, holiday


def _minute_index(start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    first = start.ceil("min")
    last = end.floor("min")
    if last >= end:
        last -= pd.Timedelta(minutes=1)
    if first > last:
        return pd.DatetimeIndex([], tz="UTC")
    return pd.date_range(first, last, freq="1min", tz="UTC" if first.tzinfo is None else None)


def _gaps_from_missing(missing: pd.DatetimeIndex) -> list[Gap]:
    if missing.empty:
        return []
    series = missing.to_series(index=range(len(missing)))
    groups = series.diff().ne(pd.Timedelta(minutes=1)).cumsum()
    gaps = []
    for _, values in series.groupby(groups):
        gaps.append(
            Gap(
                start=values.iloc[0].isoformat(),
                end=values.iloc[-1].isoformat(),
                missing_minutes=len(values),
            )
        )
    return gaps


def validate_market_data(
    frame: pd.DataFrame,
    instrument: str,
    start: datetime | pd.Timestamp | None = None,
    end: datetime | pd.Timestamp | None = None,
    holidays: Iterable[dict] | None = None,
) -> ValidationReport:
    """Validate canonical OHLCV data without filling or inventing candles."""

    report = ValidationReport(rows=len(frame))
    if frame.empty:
        if start is not None and end is not None:
            expected = _minute_index(utc_timestamp(start), utc_timestamp(end))
            open_mask, market_closed, holiday = _expected_minute_classes(
                expected, instrument, holidays
            )
            open_expected = expected[open_mask]
            report.unexpected_gaps = _gaps_from_missing(open_expected)
            report.expected_market_closed_minutes = int(market_closed.sum())
            report.expected_holiday_minutes = int(holiday.sum())
        return report

    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        report.invalid_value_rows = len(frame)
        return report

    report.duplicate_timestamps = int(frame.index.duplicated(keep=False).sum())
    diffs = frame.index.to_series().diff()
    report.out_of_order_timestamps = int((diffs.dropna() <= pd.Timedelta(0)).sum())

    invalid_ohlc = np.zeros(len(frame), dtype=bool)
    invalid_values = np.zeros(len(frame), dtype=bool)
    available_prices = []
    price_presence: dict[str, np.ndarray] = {}
    for prefix in PRICE_PREFIXES:
        columns = [f"{prefix}_{suffix}" for suffix in "ohlc"]
        if not all(column in frame.columns for column in columns):
            continue
        values = frame.loc[:, columns].apply(pd.to_numeric, errors="coerce")
        any_present = values.notna().any(axis=1).to_numpy()
        present = values.notna().all(axis=1).to_numpy()
        available_prices.append(present)
        price_presence[prefix] = present
        open_, high, low, close = (values[column].to_numpy() for column in columns)
        invalid_ohlc |= present & (
            (high < open_) | (high < close) | (low > open_) | (low > close) | (high < low)
        )
        invalid_values |= any_present & ~present
        invalid_values |= present & (
            (values <= 0).any(axis=1).to_numpy()
            | ~np.isfinite(values.to_numpy(dtype=float)).all(axis=1)
        )

    if available_prices:
        invalid_values |= ~np.logical_or.reduce(available_prices)
    else:
        invalid_values[:] = True

    any_price = np.logical_or.reduce(available_prices) if available_prices else np.zeros(len(frame), dtype=bool)
    volume_applies = {
        "bid_volume": price_presence.get("bid", np.zeros(len(frame), dtype=bool)),
        "ask_volume": price_presence.get("ask", np.zeros(len(frame), dtype=bool)),
        "volume": any_price,
    }
    for column in ("bid_volume", "ask_volume", "volume"):
        if column in frame.columns:
            volume = pd.to_numeric(frame[column], errors="coerce")
            invalid_values |= volume_applies[column] & (
                volume.isna().to_numpy()
                | ~np.isfinite(volume.to_numpy(dtype=float))
                | (volume < 0).fillna(False).to_numpy()
            )

    report.invalid_ohlc_rows = int(invalid_ohlc.sum())
    report.invalid_value_rows = int(invalid_values.sum())

    requested_start = utc_timestamp(start) if start is not None else frame.index.min()
    requested_end = utc_timestamp(end) if end is not None else frame.index.max() + pd.Timedelta(minutes=1)
    expected = _minute_index(requested_start, requested_end)
    open_mask, market_closed, holiday = _expected_minute_classes(expected, instrument, holidays)
    open_expected = expected[open_mask]
    report.expected_market_closed_minutes = int(market_closed.sum())
    report.expected_holiday_minutes = int(holiday.sum())
    actual = frame.index[(frame.index >= requested_start) & (frame.index < requested_end)].unique()
    report.unexpected_gaps = _gaps_from_missing(open_expected.difference(actual))
    return report
