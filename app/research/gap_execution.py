"""Explicit research assumptions for a position spanning missing M1 data."""
from copy import deepcopy

import pandas as pd

from app.data.gaps import GapCatalog, MINUTE, merge_events
from .data_quality import quality_fields


def simulate_trace_trade(settings, candidate, raw, observed_simulator, catalog=None):
    at = pd.Timestamp(candidate.entry_time)
    horizon = at + pd.Timedelta(minutes=settings.max_hold_minutes)
    if catalog is None:
        # Direct simulator callers can only assess the supplied interval.
        end = raw.index.max() + MINUTE if not raw.empty else at + MINUTE
        catalog = GapCatalog(min(at, raw.index.min()) if not raw.empty else at, end)
        catalog.add(candidate.pair, raw)
    trade = observed_simulator(settings, candidate, raw)
    context = deepcopy(list(candidate.missing_data_events))
    trade.update(outcome_source="OBSERVED", exit_observation_source="OBSERVED",
                 gap_policy="TRACE_NEXT_AVAILABLE_CANDLE_V1")
    if at not in raw.index:
        return _indeterminate(trade, context, "No executable entry candle")
    cutoff = pd.Timestamp(trade["exit_time"]) + MINUTE if trade["outcome"] != "TIMEOUT" else horizon + MINUTE
    gaps = catalog.select(candidate.pair, at, cutoff, "TRADE_MANAGEMENT", "STOP_LOSS", "TAKE_PROFIT")
    for gap_index, event in enumerate(gaps):
        # A prior inferred exit ends the position: later gaps must not be attached.
        end = pd.Timestamp(event["gap_end"])
        position = raw.index.searchsorted(end, side="right")
        event["resolution_method"] = "NEXT_AVAILABLE_CANDLE"
        if position >= len(raw) or raw.index[position] > horizon or raw.index[position] >= catalog.end:
            event.update(gap_resolution="UNRESOLVED", outcome_source="INDETERMINATE")
            context.append(event)
            for later in gaps[gap_index + 1:]:
                later.update(gap_resolution="UNRESOLVED", outcome_source="INDETERMINATE",
                             resolution_method="NEXT_AVAILABLE_CANDLE")
                context.append(later)
            return _indeterminate(trade, context, "No next executable candle within the loaded holding window")
        timestamp, row = raw.index[position], raw.iloc[position]
        long = candidate.direction == "long"
        side = ("bid" if long else "ask") if candidate.execution_price_mode == "bid_ask" else "mid"
        offset = ((candidate.additional_spread_pips / 2 + candidate.slippage_pips)
                  if side != "mid" else candidate.entry_spread_pips / 2) * 0.0001
        sign = -1 if long else 1
        executable = {key: float(row[f"{side}_{suffix}"]) + sign * offset
                      for key, suffix in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))}
        price, sl, tp = executable["open"], trade["sl_price"], trade["tp_price"]
        event.update(first_available_timestamp=timestamp.isoformat(),
                     details={"executable_side": side, "first_available_ohlc": executable,
                              "raw_open": float(row[f"{side}_o"]),
                              "exit_timestamp_is_observation_not_missing_hit": True})
        hit_tp = price >= tp if long else price <= tp
        hit_sl = price <= sl if long else price >= sl
        if hit_tp or hit_sl:
            resolution, source = ("TP_INFERRED", "GAP_INFERRED_TP") if hit_tp else ("SL_INFERRED", "GAP_INFERRED_SL")
            event.update(gap_resolution=resolution, outcome_source=source)
            context.append(event)
            # Scheduled closures can split missing-open-minute intervals without
            # any real observation in between. They share this first real open.
            for later in gaps[gap_index + 1:]:
                if pd.Timestamp(later["gap_start"]) >= timestamp:
                    break
                later.update(gap_resolution=resolution, outcome_source=source,
                             resolution_method="NEXT_AVAILABLE_CANDLE",
                             first_available_timestamp=timestamp.isoformat(), details=deepcopy(event["details"]))
                context.append(later)
            trade.update(outcome="WIN" if hit_tp else "LOSS", exit_time=timestamp.isoformat(),
                         exit_price=tp if hit_tp else sl,
                         r_multiple=trade["tp_pips"] / trade["sl_pips"] if hit_tp else -1.0,
                         outcome_source=source, exit_observation_source="INFERRED",
                         exit_reason=source)
            break
        event.update(gap_resolution="ASSUMED_NO_EXIT", outcome_source="GAP_ASSUMED_NO_EXIT")
        event["details"]["assumption"] = "Reopening between SL/TP: assume neither was hit in the gap; resumed candle OHLC uses normal same-bar policy."
        context.append(event)
        trade["outcome_source"] = "GAP_ASSUMED_NO_EXIT"
        # observed_simulator already handles the real resumed candle's high/low.
    events = merge_events(context)
    resolved = any(e.get("gap_resolution") in ("TP_INFERRED", "SL_INFERRED", "ASSUMED_NO_EXIT") for e in events)
    trade.update(quality_fields(events, "GAP_RESOLVED" if resolved else None))
    return trade


def _indeterminate(trade, events, reason):
    trade.update(quality_fields(events, "INDETERMINATE"))
    trade.update(outcome="INDETERMINATE", outcome_source="INDETERMINATE",
                 exit_observation_source=None, exit_time=None, exit_price=None,
                 r_multiple=None, exit_reason=reason)
    return trade
