"""Dependency annotations and aggregate quality, separate from strategy rules."""
from collections import Counter
from uuid import uuid5, NAMESPACE_URL

import pandas as pd

from app.data.gaps import CATEGORIES, MINUTE, merge_events

STATUSES = ("COMPLETE", "DEGRADED", "GAP_RESOLVED", "INDETERMINATE")


def quality_fields(events, status=None):
    events = merge_events(events)
    return dict(data_quality_status=status or ("DEGRADED" if events else "COMPLETE"),
                missing_data_events=events,
                missing_candles_count=sum(e["missing_count"] for e in events),
                missing_gap_count=len(events))


def setup_events(catalog, pair, previous, current, at, *, trigger=False,
                 zones=False, dxy=False, label_time=None, history_index=None):
    events = []
    def add(symbol, start, end, *categories):
        events.extend(catalog.select(symbol, start, min(pd.Timestamp(end), at), *categories))
    at = pd.Timestamp(at)
    add(pair, previous.start, previous.end, "SESSION_LIQUIDITY")
    add(pair, current.start, at, "SESSION_LIQUIDITY")
    for session in (previous, current):
        add(pair, session.start, session.start + MINUTE, "SESSION_OPEN")
    if dxy:
        for symbol in catalog.dxy_symbols:
            add(symbol, previous.start, previous.end, "DXY_DIVERGENCE")
            add(symbol, current.start, at, "DXY_DIVERGENCE")
    if zones:
        # Zone discovery/mitigation can retain arbitrarily old zones. A missing
        # earlier bar can hide an alternative zone; no fabricated finite lookback.
        add(pair, catalog.start, at, "HTF_ZONE")
    if trigger:
        # Pivot memory and EMA/RMA/OBV are recursive: the loaded prefix really is
        # an input. This deliberately conservative dependency is disclosed in UI.
        label = min(pd.Timestamp(label_time or at), at)
        add(pair, catalog.start, label + MINUTE, "TRIGGER", "INDICATOR")
        add(pair, label + MINUTE, at, "LABEL_CONFIRMATION")
        warmup_end = history_index[min(99, len(history_index)-1)] + MINUTE if history_index is not None and len(history_index) else at
        add(pair, catalog.start, warmup_end, "WARMUP")
    # An actual open is required. A gap immediately before it delays execution.
    preceding = catalog.select(pair, at - MINUTE, at, "ENTRY")
    events.extend(preceding)
    return merge_events(events)


def annotate_candidate(candidate, catalog, previous, current, **kwargs):
    zone_timeframes = kwargs.pop("zone_timeframes", [candidate.zone_tf])
    events = setup_events(catalog, candidate.pair, previous, current,
                          pd.Timestamp(candidate.entry_time), label_time=candidate.label_time, **kwargs)
    for event in events:
        affected = event["affected_components"]
        event["details"]["affected_timeframes"] = sorted(set(
            (["M1"] if any(c in affected for c in ("SESSION_OPEN", "SESSION_LIQUIDITY", "DXY_DIVERGENCE", "ENTRY")) else [])
            + (list(zone_timeframes) if "HTF_ZONE" in affected else [])
            + ([candidate.trigger_timeframe] if any(c in affected for c in ("TRIGGER", "INDICATOR", "LABEL_CONFIRMATION", "WARMUP")) else [])))
    candidate.missing_data_events = tuple(events)
    candidate.data_quality_status = "DEGRADED" if events else "COMPLETE"
    candidate.setup_id = str(uuid5(NAMESPACE_URL, f"{candidate.variant}|{candidate.pair}|{candidate.direction}|{candidate.break_time}|{candidate.entry_time}"))
    candidate.trace = (*candidate.trace, {"condition": "Data quality", "status": "WARN" if events else "PASS",
        "detail": f"{candidate.data_quality_status}: {len(events)} context gap(s); observed candles only, no reconstruction"})


def quality_report(physical_gaps, setups, trades):
    statuses = Counter(t.get("data_quality_status", "UNKNOWN") for t in trades)
    all_events = [e for row in [*setups, *trades] for e in row.get("missing_data_events", [])]
    categories = {category: len({e["gap_id"] for e in all_events if category in e["affected_components"]}) for category in CATEGORIES}
    symbols = {}
    for gap in physical_gaps:
        row = symbols.setdefault(gap["symbol"], {"missing_candles": 0, "gap_count": 0})
        row["missing_candles"] += gap["missing_count"]
        row["gap_count"] += 1
    resolutions = Counter(e.get("gap_resolution") for t in trades for e in t.get("missing_data_events", []))
    return dict(
        policy="TRACE_NEXT_AVAILABLE_CANDLE_V1", research_only=True,
        by_symbol=symbols, physical_gaps=physical_gaps,
        missing_candles=sum(g["missing_count"] for g in physical_gaps), gap_count=len(physical_gaps),
        affected_setups=sum(bool(s.get("missing_data_events")) for s in setups),
        evaluated_setups=len(setups), affected_trades=sum(bool(t.get("missing_data_events")) for t in trades),
        trade_status_counts={s: statuses[s] for s in STATUSES},
        setup_status_counts=dict(Counter(s.get("data_quality_status", "UNKNOWN") for s in setups)),
        categories=categories,
        gap_resolutions={key: resolutions[key] for key in ("TP_INFERRED", "SL_INFERRED", "ASSUMED_NO_EXIT", "UNRESOLVED")},
        note="ALL includes research assumptions; CLEAN filters COMPLETE trades without replaying selection. Recursive indicators/pivot memory and zone discovery depend on the loaded history. No missing prices are reconstructed.",
    )
