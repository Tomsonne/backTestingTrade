from __future__ import annotations

from datetime import datetime
from typing import Protocol

import numpy as np
import pandas as pd


CANONICAL_COLUMNS = (
    "bid_o",
    "bid_h",
    "bid_l",
    "bid_c",
    "ask_o",
    "ask_h",
    "ask_l",
    "ask_c",
    "mid_o",
    "mid_h",
    "mid_l",
    "mid_c",
    "bid_volume",
    "ask_volume",
    "volume",
)


class DataProviderError(RuntimeError):
    """Base exception for market-data provider failures."""


class DataCoverageError(DataProviderError):
    """Raised when a local dataset cannot cover the requested period safely."""


class MarketDataProvider(Protocol):
    """Small provider boundary used by the backtest engine."""

    name: str

    def get(
        self,
        instrument: str,
        start: datetime,
        end: datetime,
        interval: str = "1min",
    ) -> pd.DataFrame:
        """Return canonical, UTC, start-inclusive/end-exclusive candles."""


def utc_timestamp(value: datetime | str | pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def empty_canonical_frame() -> pd.DataFrame:
    index = pd.DatetimeIndex([], tz="UTC", name="timestamp")
    return pd.DataFrame(columns=CANONICAL_COLUMNS, index=index, dtype=float)


def normalize_canonical(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize a provider frame at the system boundary.

    Timestamps become a timezone-aware UTC index. Rows are stable-sorted and
    duplicate timestamps are resolved deterministically by keeping the last
    source row. Missing optional canonical columns are represented by NaN; no
    prices or candles are fabricated.
    """

    if frame.empty:
        return empty_canonical_frame()

    out = frame.copy()
    if "timestamp" in out.columns:
        out = out.set_index("timestamp")
    parsed = pd.to_datetime(out.index, errors="raise", utc=True)
    out.index = pd.DatetimeIndex(parsed, name="timestamp")

    for column in CANONICAL_COLUMNS:
        if column not in out.columns:
            out[column] = np.nan
        out[column] = pd.to_numeric(out[column], errors="coerce")

    out = out.loc[:, list(CANONICAL_COLUMNS)]
    out = out.sort_index(kind="mergesort")
    out = out.loc[~out.index.duplicated(keep="last")]
    return out
