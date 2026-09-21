"""Run-local, schedule-aware gap catalogue. Never changes a price frame."""
from bisect import bisect_left
from copy import copy, deepcopy
from dataclasses import asdict
import logging
from hashlib import sha256

import pandas as pd

from .validation import (VALIDATION_RUN_ID, _minute_index, expected_market_open_mask,
                         classify_provider_missing, validate_market_data)

LOGGER = logging.getLogger(__name__)

MINUTE = pd.Timedelta(minutes=1)
CATEGORIES = (
    "SESSION_OPEN", "SESSION_LIQUIDITY", "HTF_ZONE", "DXY_DIVERGENCE",
    "TRIGGER", "LABEL_CONFIRMATION", "INDICATOR", "ENTRY", "TRADE_MANAGEMENT",
    "STOP_LOSS", "TAKE_PROFIT", "WARMUP", "OTHER",
)


def merge_events(*groups):
    """One association per physical gap, even when several conditions depend on it."""
    merged = {}
    for event in (event for group in groups for event in group):
        key = event["gap_id"]
        if key not in merged:
            merged[key] = deepcopy(event)
            continue
        target = merged[key]
        components = sorted(set(target["affected_components"] + event["affected_components"]))
        start = min(pd.Timestamp(target["gap_start"]), pd.Timestamp(event["gap_start"]))
        end = max(pd.Timestamp(target["gap_end"]), pd.Timestamp(event["gap_end"]))
        target.update(deepcopy(event))
        target.update(gap_start=start.isoformat(), gap_end=end.isoformat(),
                      missing_count=int((end - start) / MINUTE) + 1,
                      affected_components=components)
    return sorted(merged.values(), key=lambda e: (e["gap_start"], e["symbol"]))


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
    def __init__(self, start, end):
        self.start, self.end = pd.Timestamp(start), pd.Timestamp(end)
        self.gaps = {}
        self._ends = {}
        self.reports = {}
        self.coverage = {}
        self.dxy_symbols = []

    def add(self, symbol, frame, holidays=(), provider_confirmed=(), *, report=None, cache_bounds=None):
        if report is None:
            report = validate_market_data(frame, symbol, self.start, self.end, holidays)
        classify_provider_missing(report, provider_confirmed)
        report.apply_policy("trace", symbol)
        self.reports[symbol] = report.to_dict()
        self.coverage[symbol] = coverage_breakdown(
            frame, symbol, self.start, self.end, report, holidays, cache_bounds)
        LOGGER.info("[run=%s] [data=%s] Gap catalogue: created; gaps=%d missing=%d",
                    VALIDATION_RUN_ID.get(), symbol, len(report.unexpected_gaps), report.unexpected_missing_minutes)
        events = []
        for gap in report.unexpected_gaps:
            key = f"{symbol}|{gap.start}|{gap.end}"
            events.append(dict(
                gap_id=sha256(key.encode()).hexdigest()[:24], symbol=symbol,
                timeframe="M1", gap_start=gap.start, gap_end=gap.end,
                missing_count=gap.missing_minutes, affected_components=[],
                first_available_timestamp=None, gap_resolution=None,
                resolution_method=None, outcome_source=None, details={},
            ))
        self.gaps[symbol] = events
        self._ends[symbol] = [pd.Timestamp(e["gap_end"]) for e in events]

    def select(self, symbol, start, end, *components):
        """Intersect [start,end). In particular never attach future gaps to a setup."""
        start, end = max(pd.Timestamp(start), self.start), min(pd.Timestamp(end), self.end)
        if end <= start:
            return []
        output = []
        events = self.gaps.get(symbol, [])
        offset = bisect_left(self._ends.get(symbol, []), start)
        for source in events[offset:]:
            a, b = pd.Timestamp(source["gap_start"]), pd.Timestamp(source["gap_end"])
            if a >= end:
                break
            a, b = max(a, start.ceil("min")), min(b, end.ceil("min") - MINUTE)
            if a > b:
                continue
            event = deepcopy(source)
            event.update(gap_start=a.isoformat(), gap_end=b.isoformat(),
                         missing_count=int((b - a) / MINUTE) + 1,
                         affected_components=list(components))
            output.append(event)
        return output

    def physical_gaps(self):
        return [deepcopy(e) for events in self.gaps.values() for e in events]


class TraceProvider:
    """A run-scoped policy wrapper, also used for every DXY source load."""
    def __init__(self, provider, catalog=None):
        self.provider = copy(provider)
        if hasattr(self.provider, "validation_mode"):
            self.provider.validation_mode = "trace"
        if hasattr(self.provider, "validation_reports"):
            self.provider.validation_reports = {}
        self.catalog = catalog

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
        if self.catalog is None:
            self.catalog = GapCatalog(start, end)
        confirmations = self.provider.store.load_confirmations(instrument, start, end) if hasattr(getattr(self.provider, "store", None), "load_confirmations") else []
        self.catalog.add(instrument, frame, metadata.get("holidays", []),
                         (r.timestamp for r in confirmations), report=report, cache_bounds=cache_bounds)
        LOGGER.info("[run=%s] [data=%s] Backtest: continued past data loading", VALIDATION_RUN_ID.get(), instrument)
        return frame
