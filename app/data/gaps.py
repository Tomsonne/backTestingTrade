"""Run-local, schedule-aware gap catalogue. Never changes a price frame."""
from bisect import bisect_left
from copy import deepcopy
from hashlib import sha256

import pandas as pd

from .validation import classify_provider_missing, validate_market_data

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


class GapCatalog:
    def __init__(self, start, end):
        self.start, self.end = pd.Timestamp(start), pd.Timestamp(end)
        self.gaps = {}
        self._ends = {}
        self.reports = {}
        self.dxy_symbols = []

    def add(self, symbol, frame, holidays=(), provider_confirmed=()):
        report = validate_market_data(frame, symbol, self.start, self.end, holidays)
        classify_provider_missing(report, provider_confirmed)
        if any((report.invalid_ohlc_rows, report.invalid_value_rows,
                report.duplicate_timestamps, report.out_of_order_timestamps)):
            # TRACE relaxes missing coverage only, never corrupt executable prices.
            raise ValueError(f"TRACE: invalid OHLC/values/order for {symbol}; repair required")
        self.reports[symbol] = report.to_dict()
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
    """Instrument every actual source load, including synthetic DXY components."""
    def __init__(self, provider, catalog):
        self.provider, self.catalog = provider, catalog
        if hasattr(provider, "validation_mode"):
            provider.validation_mode = "trace"

    def __getattr__(self, name):
        return getattr(self.provider, name)

    def get(self, instrument, start, end, interval="1min"):
        if interval not in ("1min", "M1"):
            raise ValueError("TRACE source loads must use canonical M1")
        frame = self.provider.get(instrument, start, end, interval)
        metadata = self.provider._metadata(instrument, download=False) if hasattr(self.provider, "_metadata") else {}
        confirmations = self.provider.store.load_confirmations(instrument, self.catalog.start, self.catalog.end) if hasattr(getattr(self.provider, "store", None), "load_confirmations") else []
        self.catalog.add(instrument, frame, (metadata or {}).get("holidays", []), (r.timestamp for r in confirmations))
        return frame
