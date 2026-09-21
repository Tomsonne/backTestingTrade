"""Coverage-only TRACE adapter. No prices, strategy or trade outcomes are changed."""
from copy import copy
from dataclasses import asdict
import logging

import pandas as pd

from .validation import (VALIDATION_RUN_ID, _minute_index, expected_market_open_mask,
                         validate_market_data)

LOGGER = logging.getLogger(__name__)


def coverage_breakdown(frame, symbol, start, end, report, holidays=(), cache_bounds=None):
    expected = _minute_index(pd.Timestamp(start), pd.Timestamp(end))
    expected = expected[expected_market_open_mask(expected, symbol, holidays)]
    actual = expected.intersection(frame.index)
    missing = expected.difference(actual)
    def empty_buckets(rule, date_format):
        expected_counts = pd.Series(1, index=expected, dtype="int64").resample(rule, closed="left", label="left").sum()
        actual_counts = pd.Series(1, index=actual, dtype="int64").resample(rule, closed="left", label="left").sum()
        empty = expected_counts[(expected_counts > 0) & (actual_counts.reindex(expected_counts.index, fill_value=0) == 0)]
        return [t.strftime(date_format) for t in empty.index]
    # Buckets below describe the requested portion of each UTC period. A partial
    # first/last bucket is explicitly not a claim about its unrequested portion.
    gaps = [asdict(g) for g in report.unexpected_gaps]
    first = pd.Timestamp(report.first_available_candle) if report.first_available_candle else None
    last = pd.Timestamp(report.last_available_candle) if report.last_available_candle else None
    internal = [g for g in gaps if first is not None and pd.Timestamp(g["start"]) > first and pd.Timestamp(g["end"]) < last]
    return {
        "symbol": symbol, **report.to_dict(),
        "largest_gap": max(gaps, key=lambda g: g["missing_minutes"], default=None),
        "small_internal_gaps": {"limit_minutes": 5, "events": sum(g["missing_minutes"] <= 5 for g in internal),
                                "missing_minutes": sum(g["missing_minutes"] for g in internal if g["missing_minutes"] <= 5)},
        "fully_missing_requested_days_utc": empty_buckets("D", "%Y-%m-%d"),
        "fully_missing_requested_weeks_utc": empty_buckets("W-MON", "%Y-%m-%d"),
        "fully_missing_requested_months_utc": empty_buckets("MS", "%Y-%m"),
        "missing_before_first_available": int((missing < first).sum()) if first is not None else len(missing),
        "missing_after_last_available": int((missing > last).sum()) if last is not None else 0,
        "cache_bounds": cache_bounds or {},
        "note": "UTC buckets cover only the requested interval. Scheduled closures are excluded. No reconstruction; absent history yields no usable setup data. This is source coverage, not a trade-quality or gap-exit model.",
    }


class GapCatalog:
    def __init__(self):
        self.coverage = {}

    def add(self, symbol, frame, start, end, report, holidays=(), cache_bounds=None):
        self.coverage[symbol] = coverage_breakdown(frame, symbol, start, end, report, holidays, cache_bounds)
        LOGGER.info("[run=%s] [data=%s] Gap catalogue: created; gaps=%d missing=%d",
                    VALIDATION_RUN_ID.get(), symbol, len(report.unexpected_gaps), report.unexpected_missing_minutes)


class TraceProvider:
    """A run-scoped policy wrapper, also used for every DXY source load."""
    def __init__(self, provider):
        self.provider = copy(provider)
        if hasattr(self.provider, "validation_mode"):
            self.provider.validation_mode = "trace"
        if hasattr(self.provider, "validation_reports"):
            self.provider.validation_reports = {}
        self.catalog = GapCatalog()

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def get(self, instrument, start, end, interval="1min"):
        from .dukascopy import DukascopyProvider
        if interval not in {"1min", "M1"}:
            raise ValueError("TRACE catalogue requires canonical M1 source loads")
        if isinstance(self.provider, DukascopyProvider):
            frame = self.provider.get(instrument, start, end, interval, validation_policy="trace")
            report = self.provider.validation_reports[instrument]
            metadata = self.provider._metadata(instrument, download=False) or {}
            manifest = self.provider.store.load_manifest(instrument)
            cache_bounds = {key: manifest.get(key) for key in ("start", "end", "rows")}
        else:
            frame = self.provider.get(instrument, start, end, interval)
            metadata = self.provider._metadata(instrument, download=False) or {} if hasattr(self.provider, "_metadata") else {}
            report = validate_market_data(frame, instrument, start, end, metadata.get("holidays", []))
            report.apply_policy("trace", instrument)
            cache_bounds = {}
        self.catalog.add(instrument, frame, start, end, report, metadata.get("holidays", []), cache_bounds)
        LOGGER.info("[run=%s] [data=%s] Backtest: continued past data loading", VALIDATION_RUN_ID.get(), instrument)
        return frame
