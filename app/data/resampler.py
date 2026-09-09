from __future__ import annotations

import pandas as pd

from .base import CANONICAL_COLUMNS, normalize_canonical


INTERVAL_RULES = {
    "1min": "1min",
    "M1": "1min",
    "5min": "5min",
    "M5": "5min",
    "H1": "1h",
    "H2": "2h",
    "H4": "4h",
}


def _aggregate_column(resampler, column: str, operation: str) -> pd.Series:
    grouped = resampler[column]
    if operation == "first":
        return grouped.first()
    if operation == "last":
        return grouped.last()
    if operation == "max":
        return grouped.max()
    if operation == "min":
        return grouped.min()
    return grouped.sum(min_count=1)


def resample_market_data(
    m1: pd.DataFrame,
    interval: str,
    anchor_timezone: str = "UTC",
    drop_incomplete_last: bool = True,
) -> pd.DataFrame:
    """Build deterministic OHLCV candles from canonical M1 data.

    Bins are anchored to local midnight in ``anchor_timezone``. This preserves
    the project's historical H2/H4 behavior when set to ``Europe/Paris`` while
    making the alignment explicit and configurable.
    """

    if interval not in INTERVAL_RULES:
        raise ValueError(f"Unsupported interval: {interval}")
    rule = INTERVAL_RULES[interval]
    frame = normalize_canonical(m1)
    if frame.empty or rule == "1min":
        return frame.copy()

    local = frame.tz_convert(anchor_timezone)
    grouped = local.resample(rule, origin="start_day", label="left", closed="left")
    operations = {
        "bid_o": "first",
        "bid_h": "max",
        "bid_l": "min",
        "bid_c": "last",
        "ask_o": "first",
        "ask_h": "max",
        "ask_l": "min",
        "ask_c": "last",
        "mid_o": "first",
        "mid_h": "max",
        "mid_l": "min",
        "mid_c": "last",
        "bid_volume": "sum",
        "ask_volume": "sum",
        "volume": "sum",
    }
    out = pd.DataFrame(
        {column: _aggregate_column(grouped, column, operations[column]) for column in CANONICAL_COLUMNS}
    )
    out = out.dropna(how="all", subset=["mid_o", "mid_h", "mid_l", "mid_c"])

    if drop_incomplete_last and not out.empty:
        duration = pd.Timedelta(rule)
        last_complete_at = local.index.max() + pd.Timedelta(minutes=1)
        if out.index[-1] + duration > last_complete_at:
            out = out.iloc[:-1]

    out.index = out.index.tz_convert("UTC")
    out.index.name = "timestamp"
    return normalize_canonical(out)
