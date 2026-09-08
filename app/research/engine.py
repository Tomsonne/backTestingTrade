from __future__ import annotations

from collections import Counter
from bisect import bisect_left
from copy import copy
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Callable

import pandas as pd

from app.backtest import (
    Candidate,
    _dxy_nonconfirm,
    _entry_price,
    _ext,
    _mid,
    _strategy_timestamp,
    run_backtest,
    simulate_trade,
)
from app.config import SessionDef, Settings
from app.data.base import DataCoverageError, MarketDataProvider, utc_timestamp
from app.data.factory import create_provider
from app.data.resampler import resample_market_data
from app.data.validation import validate_market_data
from app.dxy import DXY_COMPONENTS, direct_dxy, synthetic_dxy
from app.m1_divergence import LabelEvent, build_label_events
from app.sessions import SessionInstance, build_session_instances, session_slice
from app.zones import Zone, build_zones

from .analysis import build_analysis
from .config import StrategyConfig
from .presets import ensure_legacy_compatible


Progress = Callable[[str, float], None]


@dataclass
class ResearchRunOutput:
    result: dict[str, Any]
    trades: list[dict[str, Any]]
    rejected: list[dict[str, Any]]
    stats: list[dict[str, Any]]
    candle_count: int
    data_warnings: list[dict[str, Any] | str]


def _progress(callback: Progress | None, step: str, percent: float) -> None:
    if callback:
        callback(step, percent)


def runtime_settings(base: Settings, config: StrategyConfig) -> Settings:
    runtime = copy(base)
    runtime.dxy_source = config.instruments.dxy_source
    runtime.execution_price_mode = config.execution.price_mode
    runtime.volume_mode = config.volume_mode
    runtime.data_validation_mode = config.validation.mode
    runtime.htf_anchor_timezone = config.htf_anchor_timezone
    runtime.timezone = config.timezone
    runtime.sessions_json = json.dumps(
        [
            {
                "name": item.name,
                "start": item.start.strftime("%H:%M"),
                "end": item.end.strftime("%H:%M"),
                "timezone": item.timezone or config.timezone,
            }
            for item in config.active_sessions
        ]
    )
    runtime.backtest_start = config.period.start.isoformat()
    runtime.backtest_end = (config.period.end + timedelta(days=1)).isoformat()
    runtime.imbalance_min_adr_pct = config.zones.imbalance_min_adr_pct
    runtime.adr_length = config.zones.adr_length
    runtime.zone_direction_match = config.zones.direction_match
    runtime.lb = config.trigger.left_bars
    runtime.rb = config.trigger.right_bars
    runtime.showlimit = config.trigger.indicators.minimum_divergent
    runtime.check_cut_through = config.trigger.check_cut_through
    runtime.invalidate_if_dxy_confirms_before_entry = config.divergence.invalidate_if_dxy_confirms
    runtime.eurusd_sl_pips = config.exits.EUR_USD.stop_loss_pips
    runtime.eurusd_tp_pips = config.exits.EUR_USD.take_profit_pips
    runtime.gbpusd_sl_pips = config.exits.GBP_USD.stop_loss_pips
    runtime.gbpusd_tp_pips = config.exits.GBP_USD.take_profit_pips
    runtime.first_trade_risk_pct = config.risk.first_trade_risk_pct
    runtime.second_trade_risk_pct = config.risk.second_trade_risk_pct
    runtime.starting_equity = config.risk.starting_equity
    runtime.max_hold_minutes = config.exits.max_hold_minutes
    runtime.same_bar_policy = config.execution.same_bar_policy
    runtime.pair_priority = tuple(config.risk.pair_priority)
    return runtime


def _trace(condition: str, status: str, detail: str) -> dict[str, str]:
    return {"condition": condition, "status": status, "detail": detail}


def _rejection(
    pair: str,
    session: SessionInstance,
    reason: str,
    trace: list[dict[str, str]],
    direction: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "pair": pair,
        "trade_date": session.trade_date.isoformat(),
        "session": session.name,
        "direction": direction,
        "reason": reason,
        "trace": trace,
        **extra,
    }


def _breaks_configurable(
    frame: pd.DataFrame,
    current: SessionInstance,
    previous_high: float,
    previous_low: float,
    config: StrategyConfig,
) -> dict[str, pd.Timestamp]:
    sliced = session_slice(frame, current)
    tolerance = config.divergence.tolerance_pips * 0.0001
    output: dict[str, pd.Timestamp] = {}
    if config.divergence.break_high:
        rows = sliced[sliced.mid_h > previous_high + tolerance]
        if not rows.empty:
            output["short"] = rows.index[0]
    if config.divergence.break_low:
        rows = sliced[sliced.mid_l < previous_low - tolerance]
        if not rows.empty:
            output["long"] = rows.index[0]
    return output


def _previous_session(
    sessions: list[SessionInstance],
    index: int,
    config: StrategyConfig,
) -> SessionInstance | None:
    if config.divergence.previous_session == "chronological":
        return sessions[index - 1] if index else None
    wanted = config.divergence.previous_session_name
    for candidate in reversed(sessions[:index]):
        if candidate.name == wanted:
            return candidate
    return None


def _trigger_events(mid: pd.DataFrame, config: StrategyConfig) -> list[dict[str, Any]]:
    if config.trigger.mode == "NONE":
        return []
    output: list[dict[str, Any]] = []
    for timeframe in config.trigger.timeframes:
        trigger_frame = mid if timeframe == "M1" else resample_market_data(
            mid, timeframe, config.htf_anchor_timezone
        )
        events = build_label_events(
            trigger_frame,
            config.trigger.left_bars,
            config.trigger.right_bars,
            config.trigger.indicators.minimum_divergent,
            config.trigger.check_cut_through,
            config.trigger.indicators.enabled,
            config.trigger.indicators.required,
        )
        for event in events:
            if config.trigger.mode == "FAST":
                entry_index = event.a_entry_index
            else:
                entry_index = event.b_entry_index
            if entry_index is None or entry_index >= len(trigger_frame):
                continue
            entry_time = trigger_frame.index[entry_index]
            raw_index = mid.index.searchsorted(entry_time, side="left")
            if raw_index >= len(mid):
                continue
            output.append(
                {
                    "event": event,
                    "entry_time": mid.index[raw_index],
                    "timeframe": timeframe,
                }
            )
    return sorted(output, key=lambda item: (item["entry_time"], item["timeframe"]))


def _zone_duration(timeframe: str) -> pd.Timedelta:
    return pd.Timedelta(hours={"H1": 1, "H2": 2, "H4": 4}[timeframe])


def _zone_retests(zone: Zone, mid: pd.DataFrame, at: pd.Timestamp) -> int:
    history = mid[(mid.index >= zone.formed_at) & (mid.index < at)]
    inside = (history.mid_h >= zone.bottom) & (history.mid_l <= zone.top)
    return int((inside & ~inside.shift(1, fill_value=False)).sum())


def _eligible_zone(
    zone: Zone,
    at: pd.Timestamp,
    price: float,
    direction: str,
    mid: pd.DataFrame,
    config: StrategyConfig,
    direction_match: bool,
) -> bool:
    wanted = "bullish" if direction == "long" else "bearish"
    if direction_match and zone.direction != wanted:
        return False
    if zone.formed_at > at or (zone.invalidated_at is not None and at >= zone.invalidated_at):
        return False
    if config.zones.expires_after_bars is not None:
        expiry = zone.formed_at + _zone_duration(zone.timeframe) * config.zones.expires_after_bars
        if at >= expiry:
            return False
    distance = 0.0 if zone.bottom <= price <= zone.top else min(abs(price - zone.bottom), abs(price - zone.top)) / 0.0001
    if config.zones.max_distance_pips is None:
        if distance > 0:
            return False
    elif distance > config.zones.max_distance_pips:
        return False
    retests = _zone_retests(zone, mid, at)
    if not config.zones.allow_touched and retests:
        return False
    if config.zones.max_retests is not None and retests > config.zones.max_retests:
        return False
    return True


def _select_zones(
    zones: dict[str, list[Zone]],
    at: pd.Timestamp,
    price: float,
    direction: str,
    mid: pd.DataFrame,
    config: StrategyConfig,
    direction_match: bool | None = None,
) -> list[Zone]:
    if not config.zones.timeframes:
        return []
    match_direction = config.zones.direction_match if direction_match is None else direction_match
    found: dict[str, Zone] = {}
    for timeframe in config.zones.timeframes:
        eligible = [
            zone for zone in zones[timeframe]
            if _eligible_zone(zone, at, price, direction, mid, config, match_direction)
        ]
        if eligible:
            found[timeframe] = max(eligible, key=lambda zone: zone.formed_at)
    if config.zones.match_mode == "ALL" and len(found) != len(config.zones.timeframes):
        return []
    for timeframe in config.zones.priority:
        if timeframe in found:
            ordered = [found[timeframe]]
            ordered.extend(found[key] for key in config.zones.priority if key in found and key != timeframe)
            return ordered
    return list(found.values())


def _dxy_has_gap(dxy: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> bool:
    sliced = dxy[(dxy.index >= start) & (dxy.index < end)]
    if sliced.empty:
        return True
    return bool((sliced.index.to_series().diff().dropna() > pd.Timedelta(minutes=1)).any())


def _entry_details(
    raw: pd.DataFrame,
    at: pd.Timestamp,
    direction: str,
    config: StrategyConfig,
) -> tuple[float, float, str, float | None, float | None]:
    if config.trigger.confirmation_type == "signal_close":
        position = raw.index.searchsorted(at, side="left") - 1
        if position < 0:
            raise ValueError("signal_close entry requires a completed preceding candle")
        row = raw.iloc[position]
        bid = float(row.bid_c) if pd.notna(row.bid_c) else None
        ask = float(row.ask_c) if pd.notna(row.ask_c) else None
        if config.execution.price_mode == "bid_ask" and bid is not None and ask is not None:
            entry = ask if direction == "long" else bid
            raw_spread = (ask - bid) / 0.0001
            mode = "bid_ask"
        else:
            entry = float(row.mid_c)
            raw_spread = 0.0
            mode = "mid"
    else:
        entry, raw_spread, mode = _entry_price(raw, at, direction, config.execution.price_mode, 0)
        row = raw.loc[at]
        bid = float(row.bid_o) if "bid_o" in row and pd.notna(row.bid_o) else None
        ask = float(row.ask_o) if "ask_o" in row and pd.notna(row.ask_o) else None
    adverse = (config.execution.additional_spread_pips / 2 + config.execution.slippage_pips) * 0.0001
    entry += adverse if direction == "long" else -adverse
    return entry, raw_spread + config.execution.additional_spread_pips, mode, bid, ask


def _build_candidates(
    config: StrategyConfig,
    runtime: Settings,
    pair: str,
    raw: pd.DataFrame,
    dxy: pd.DataFrame | None,
) -> tuple[list[Candidate], list[dict[str, Any]]]:
    mid = _mid(raw, config.volume_mode)
    if mid.empty:
        return [], []
    zones = {
        timeframe: build_zones(
            mid, timeframe, config.htf_anchor_timezone,
            config.zones.imbalance_min_adr_pct, config.zones.adr_length,
            config.zones.mitigation_mode,
        )
        for timeframe in config.zones.timeframes
    }
    events = _trigger_events(mid, config)
    events_by_direction = {
        direction: [item for item in events if item["event"].direction == direction]
        for direction in ("short", "long")
    }
    event_times = {
        # Trigger frames are left-labelled. Eligibility is based on the
        # timestamp where the complete signal can actually be traded, not the
        # visual timestamp of its source candle (especially important on M5).
        direction: [item["entry_time"] for item in items]
        for direction, items in events_by_direction.items()
    }
    definitions = [
        SessionDef(item.name, item.start, item.end, item.timezone or config.timezone)
        for item in config.active_sessions
    ]
    sessions = build_session_instances(
        mid.index.min().to_pydatetime(), mid.index.max().to_pydatetime(), definitions, config.timezone
    )
    sessions = [item for item in sessions if not session_slice(mid, item).empty]
    candidates: list[Candidate] = []
    rejected: list[dict[str, Any]] = []

    for index, current in enumerate(sessions):
        if current.trade_date.weekday() >= 5:
            continue
        if not (config.period.start <= current.trade_date <= config.period.end):
            continue
        previous = _previous_session(sessions, index, config)
        if previous is None:
            continue
        data_end = mid.index.max() + pd.Timedelta(minutes=1)
        if previous.start < mid.index.min() or previous.end > data_end:
            rejected.append(_rejection(pair, current, "PREVIOUS_SESSION_DATA_MISSING", [
                _trace(
                    "Previous session levels",
                    "FAIL",
                    "The selected previous session is not fully inside the loaded M1 range",
                )
            ]))
            continue
        pair_extrema = _ext(mid, previous)
        if pair_extrema is None:
            rejected.append(_rejection(pair, current, "PREVIOUS_SESSION_DATA_MISSING", [
                _trace("Previous session levels", "FAIL", "No pair candles in the selected previous session")
            ]))
            continue
        previous_high, previous_low = pair_extrema
        breaks = _breaks_configurable(mid, current, previous_high, previous_low, config)
        base_trace = [_trace(
            "Previous session levels", "PASS",
            f"{previous.name}: high={previous_high:.6f}, low={previous_low:.6f}",
        )]
        if not breaks:
            rejected.append(_rejection(
                pair, current, "NO_PAIR_BREAK",
                base_trace + [_trace("Pair break", "FAIL", "Neither enabled level was broken")],
            ))
            continue

        dxy_extrema = _ext(dxy, previous, True) if config.divergence.enabled and dxy is not None else None
        for direction, break_time in breaks.items():
            level = previous_high if direction == "short" else previous_low
            break_price = float(mid.loc[break_time, "mid_h" if direction == "short" else "mid_l"])
            trace = base_trace + [_trace(
                "Pair break", "PASS", f"{direction} break at {break_time.isoformat()} ({break_price:.6f})"
            )]
            dxy_at_break = None
            if config.divergence.enabled:
                if dxy is None or dxy_extrema is None or _dxy_has_gap(dxy, current.start, break_time + pd.Timedelta(minutes=1)):
                    behavior = config.divergence.dxy_gap_behavior
                    trace.append(_trace("DXY data", "FAIL" if behavior == "reject" else "WARN", "Missing DXY M1 coverage"))
                    if behavior == "reject":
                        rejected.append(_rejection(pair, current, "DXY_DATA_MISSING", trace, direction,
                                                   break_time=break_time.isoformat()))
                        continue
                else:
                    dxy_high, dxy_low = dxy_extrema
                    # Timestamps identify M1 candle opens. Intrabar confirmation
                    # includes the now-closed break candle; interbar only uses
                    # candles that closed before it. Both cutoffs are exclusive
                    # inside _dxy_nonconfirm and therefore never read the future.
                    cutoff = break_time + (
                        pd.Timedelta(minutes=1)
                        if config.divergence.confirmation == "intrabar"
                        else pd.Timedelta(0)
                    )
                    nonconfirm = _dxy_nonconfirm(dxy, current, cutoff, direction, dxy_high, dxy_low)
                    dxy_row = dxy.loc[:break_time].iloc[-1] if not dxy.loc[:break_time].empty else None
                    dxy_at_break = float(dxy_row.close) if dxy_row is not None else None
                    if not nonconfirm:
                        trace.append(_trace("DXY non-confirmation", "FAIL", "DXY confirmed the pair break"))
                        rejected.append(_rejection(pair, current, "DXY_CONFIRMED", trace, direction,
                                                   break_time=break_time.isoformat()))
                        continue
                    trace.append(_trace(
                        "DXY non-confirmation",
                        "PASS",
                        f"No confirming DXY level break ({config.divergence.confirmation})",
                    ))
            else:
                trace.append(_trace("DXY divergence", "SKIPPED", "Disabled by configuration"))

            start_at = bisect_left(event_times[direction], break_time)
            possible = events_by_direction[direction][start_at:]
            if config.trigger.mode == "NONE":
                entry_position = mid.index.searchsorted(break_time + pd.Timedelta(minutes=1), side="left")
                possible = [] if entry_position >= len(mid) else [{
                    "event": LabelEvent(entry_position, break_time, direction, 0),
                    "entry_time": mid.index[entry_position], "timeframe": "NONE",
                }]
            if not possible:
                rejected.append(_rejection(
                    pair, current, "NO_LABEL",
                    trace + [_trace("Lower-timeframe trigger", "FAIL", "No eligible divergence label")],
                    direction, break_time=break_time.isoformat(),
                ))
                continue

            accepted = False
            last_failure: dict[str, Any] | None = None
            for item in possible:
                event: LabelEvent = item["event"]
                entry_time: pd.Timestamp = item["entry_time"]
                event_trace = list(trace)
                if config.divergence.max_delay_minutes is not None:
                    divergence_delay = (entry_time - break_time) / pd.Timedelta(minutes=1)
                    if divergence_delay > config.divergence.max_delay_minutes:
                        last_failure = _rejection(
                            pair, current, "LABEL_TOO_LATE",
                            event_trace + [_trace(
                                "Divergence window", "FAIL", f"{divergence_delay:.0f} minutes"
                            )],
                            direction, break_time=break_time.isoformat(), label_time=event.time.isoformat(),
                        )
                        break
                if config.trigger.max_delay_minutes is not None:
                    delay = (entry_time - break_time) / pd.Timedelta(minutes=1)
                    if delay > config.trigger.max_delay_minutes:
                        last_failure = _rejection(
                            pair, current, "LABEL_TOO_LATE",
                            event_trace + [_trace("Trigger delay", "FAIL", f"{delay:.0f} minutes")],
                            direction, break_time=break_time.isoformat(), label_time=event.time.isoformat(),
                        )
                        break
                if entry_time >= current.end:
                    last_failure = _rejection(
                        pair, current, "ENTRY_OUTSIDE_SESSION",
                        event_trace + [_trace("Entry", "FAIL", "Entry would occur after session end")],
                        direction, break_time=break_time.isoformat(), label_time=event.time.isoformat(),
                    )
                    break
                if config.divergence.enabled and config.divergence.invalidate_if_dxy_confirms and dxy_extrema:
                    if not _dxy_nonconfirm(dxy, current, entry_time, direction, *dxy_extrema):
                        last_failure = _rejection(
                            pair, current, "DXY_CONFIRMED",
                            event_trace + [_trace("DXY before entry", "FAIL", "DXY confirmed before entry")],
                            direction, break_time=break_time.isoformat(), label_time=event.time.isoformat(),
                        )
                        continue
                event_trace.append(_trace(
                    "Lower-timeframe trigger", "PASS",
                    f"{item['timeframe']} {config.trigger.mode}: {event.count} indicator(s)",
                ))
                price = float(mid.loc[entry_time, "mid_o"])
                selected = _select_zones(zones, entry_time, price, direction, mid, config)
                if config.zones.timeframes and not selected:
                    without_direction = _select_zones(
                        zones, entry_time, price, direction, mid, config, direction_match=False
                    )
                    reason = "ZONE_WRONG_DIRECTION" if without_direction and config.zones.direction_match else "NO_ZONE"
                    last_failure = _rejection(
                        pair, current, reason,
                        event_trace + [_trace("HTF zones", "FAIL", reason.replace("_", " ").title())],
                        direction, break_time=break_time.isoformat(), label_time=event.time.isoformat(),
                    )
                    continue
                if selected:
                    event_trace.append(_trace(
                        "HTF zones", "PASS", ", ".join(zone.timeframe for zone in selected)
                    ))
                    chosen = selected[0]
                else:
                    event_trace.append(_trace("HTF zones", "SKIPPED", "No zone required"))
                    chosen = Zone("NONE", "none", price, price, entry_time, None, 0)
                entry, spread, mode, bid, ask = _entry_details(raw, entry_time, direction, config)
                event_trace.append(_trace("Entry", "PASS", f"{mode} at {entry:.6f}"))
                active_zones = tuple(
                    {
                        "timeframe": zone.timeframe, "direction": zone.direction,
                        "bottom": zone.bottom, "top": zone.top,
                        "formed_at": zone.formed_at.isoformat(),
                    }
                    for zone in selected
                )
                candidates.append(
                    Candidate(
                        config.preset_name or config.name, pair, direction, current.trade_date.isoformat(),
                        current.name, previous.name, break_time.isoformat(), event.time.isoformat(),
                        event.count, entry_time.isoformat(), entry, chosen.timeframe, chosen.bottom,
                        chosen.top, chosen.formed_at.isoformat(),
                        float(dxy_extrema[0]) if dxy_extrema else float("nan"),
                        float(dxy_extrema[1]) if dxy_extrema else float("nan"),
                        previous_high, previous_low, mode, spread, level, break_price, dxy_at_break,
                        True, item["timeframe"], event.indicators, active_zones, tuple(event_trace), bid, ask,
                        config.execution.additional_spread_pips, config.execution.slippage_pips,
                        entry_time.isoformat(),
                    )
                )
                accepted = True
                break
            if not accepted and last_failure:
                rejected.append(last_failure)
    return candidates, rejected


def _risk_rejection(candidate: Candidate, reason: str, detail: str) -> dict[str, Any]:
    return {
        "pair": candidate.pair, "trade_date": candidate.trade_date,
        "session": candidate.session, "direction": candidate.direction, "reason": reason,
        "entry_time": candidate.entry_time,
        "trace": list(candidate.trace) + [_trace("Risk management", "FAIL", detail)],
    }


def _apply_risk(
    config: StrategyConfig,
    runtime: Settings,
    candidates: list[Candidate],
    raw: dict[str, pd.DataFrame],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rank = {pair: index for index, pair in enumerate(config.risk.pair_priority)}
    ordered = sorted(candidates, key=lambda item: (
        item.trade_date, pd.Timestamp(item.entry_time), rank.get(item.pair, 999)
    ))
    equity = config.risk.starting_equity
    trades: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    by_day: dict[str, list[dict[str, Any]]] = {}

    for candidate in ordered:
        day_trades = by_day.setdefault(candidate.trade_date, [])
        if len(day_trades) >= config.risk.max_trades_per_day:
            rejected.append(_risk_rejection(candidate, "DAILY_LIMIT_REACHED", "Maximum trades/day reached"))
            continue
        if day_trades and config.risk.stop_after_win and any(item["outcome"] == "WIN" for item in day_trades):
            rejected.append(_risk_rejection(candidate, "DAILY_LIMIT_REACHED", "Trading stops after a win"))
            continue
        if day_trades and day_trades[-1]["outcome"] == "LOSS" and not config.risk.second_trade_after_loss:
            rejected.append(_risk_rejection(candidate, "DAILY_LIMIT_REACHED", "Second trade after loss disabled"))
            continue
        if config.risk.one_position_at_a_time and trades:
            previous = trades[-1]
            if pd.Timestamp(candidate.entry_time) <= pd.Timestamp(previous["exit_time"]):
                rejected.append(_risk_rejection(candidate, "POSITION_ALREADY_OPEN", "Previous position not closed"))
                continue
        risk = config.risk.first_trade_risk_pct if not day_trades else config.risk.second_trade_risk_pct
        used_risk = sum(float(item["risk_pct"]) for item in day_trades)
        if used_risk + risk > config.risk.max_daily_risk_pct:
            rejected.append(_risk_rejection(candidate, "DAILY_LIMIT_REACHED", "Daily risk budget exceeded"))
            continue
        trade = simulate_trade(runtime, candidate, raw[candidate.pair])
        trade_number = len(day_trades) + 1
        return_pct = trade["r_multiple"] * risk
        before = equity
        equity *= 1 + return_pct / 100
        trade.update(
            trade_number_day=trade_number,
            risk_pct=risk,
            equity_before=before,
            return_pct=return_pct,
            equity_after=equity,
            pnl=equity - before,
            exit_reason=trade["outcome"],
            trace=list(candidate.trace) + [_trace("Simulation", "PASS", trade["outcome"])],
        )
        trades.append(trade)
        day_trades.append(trade)
    return trades, rejected


def _synthetic_from_provider(
    provider: MarketDataProvider,
    pair_frames: dict[str, pd.DataFrame],
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, int]:
    components: dict[str, pd.DataFrame] = {}
    loaded = 0
    for symbol in DXY_COMPONENTS:
        frame = pair_frames.get(symbol)
        if frame is None:
            frame = provider.get(symbol, start, end, "1min")
            loaded += len(frame)
        components[symbol] = frame
    return synthetic_dxy(components), loaded


def _quality_report(
    provider: MarketDataProvider,
    instrument: str,
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> dict[str, Any]:
    metadata = {}
    if hasattr(provider, "_metadata"):
        metadata = provider._metadata(instrument, download=False) or {}  # type: ignore[attr-defined]
    report = validate_market_data(frame, instrument, start, end, metadata.get("holidays", []))
    payload = report.to_dict()
    gaps = payload.get("unexpected_gaps", [])
    payload["gap_event_count"] = len(gaps)
    payload["unexpected_gaps"] = gaps[:100]
    payload["gap_details_truncated"] = len(gaps) > 100
    return {"instrument": instrument, **payload}


def _legacy_output(
    config: StrategyConfig,
    runtime: Settings,
    provider: MarketDataProvider | None,
    now: pd.Timestamp,
    progress: Progress | None,
) -> ResearchRunOutput:
    _progress(progress, "LOADING_DATA", 10)
    result = run_backtest(runtime, provider=provider, now=now)
    _progress(progress, "CALCULATING_STATISTICS", 85)
    variant = config.preset_name.split()[-1] if config.preset_name else None
    trades = [item for item in result["trades"] if variant is None or item["variant"] == variant]
    for trade in trades:
        trade.setdefault("trigger_timeframe", "M1")
        trade.setdefault("indicators_diverged", [])
        if not trade.get("trace"):
            trade["trace"] = [
                _trace("Legacy engine", "PASS", "Historical A/B rules preserved exactly")
            ]
    analysis = build_analysis(trades, config)
    legacy_summary = next((item for item in result["summary"] if item["variant"] == variant), {})
    summary = {**analysis["overall"], **legacy_summary}
    research_result = {
        **result,
        "name": config.name,
        "config_hash": config.config_hash,
        "strategy_version": config.strategy_version,
        "selected_variant": variant,
        "summary": summary,
        "trades": trades,
        "candidate_count": (result.get("candidate_counts") or {}).get(
            variant, result.get("candidate_count", 0)
        ),
        "trade_count": len(trades),
        "analysis": {key: value for key, value in analysis.items() if key != "records"},
        "rejected_counts": {},
    }
    return ResearchRunOutput(
        research_result, trades, [], analysis["records"], int(result.get("candle_count", 0)), []
    )


def run_research_backtest(
    config: StrategyConfig,
    settings: Settings,
    provider: MarketDataProvider | None = None,
    now: datetime | pd.Timestamp | None = None,
    progress: Progress | None = None,
) -> ResearchRunOutput:
    ensure_legacy_compatible(config)
    runtime = runtime_settings(settings, config)
    current = utc_timestamp(now or datetime.now(timezone.utc))
    if config.strategy_version == "legacy-ab-v1" and config.preset_name in {
        "Legacy A.0", "Legacy A.1", "Legacy B.0", "Legacy B.1"
    }:
        return _legacy_output(config, runtime, provider, current, progress)

    provider = provider or create_provider(runtime)
    if hasattr(provider, "validation_mode"):
        provider.validation_mode = config.validation.mode  # type: ignore[attr-defined]
    start = _strategy_timestamp(config.period.start.isoformat(), runtime)
    end = _strategy_timestamp((config.period.end + timedelta(days=1)).isoformat(), runtime)

    _progress(progress, "LOADING_DATA", 10)
    raw = {pair: provider.get(pair, start, end, "1min") for pair in config.instruments.pairs}
    candle_count = sum(len(frame) for frame in raw.values())
    warnings: list[dict[str, Any] | str] = []
    _progress(progress, "VALIDATING_DATA", 22)
    for pair, frame in raw.items():
        quality = _quality_report(provider, pair, frame, start, end)
        if not quality["is_valid"]:
            warnings.append(quality)
            if (
                config.validation.mode == "permissive"
                and quality["unexpected_missing_minutes"] > config.validation.permissive_gap_limit_minutes
            ):
                raise DataCoverageError(
                    f"Permissive gap limit exceeded for {pair}: "
                    f"{quality['unexpected_missing_minutes']} missing minute(s)"
                )

    dxy: pd.DataFrame | None = None
    dxy_source = "disabled"
    revision_symbols = list(config.instruments.pairs)
    if config.divergence.enabled:
        if config.instruments.dxy_source == "dukascopy_direct":
            try:
                dxy_frame = provider.get(config.instruments.dxy_instrument, start, end, "1min")
                candle_count += len(dxy_frame)
                dxy = direct_dxy(dxy_frame)
                dxy_source = "dukascopy_direct"
                revision_symbols.append(config.instruments.dxy_instrument)
            except DataCoverageError:
                if not config.instruments.synthetic_fallback:
                    raise
                dxy, loaded = _synthetic_from_provider(provider, raw, start, end)
                candle_count += loaded
                dxy_source = "synthetic_fallback"
                revision_symbols.extend(DXY_COMPONENTS)
        else:
            dxy, loaded = _synthetic_from_provider(provider, raw, start, end)
            candle_count += loaded
            dxy_source = "synthetic"
            revision_symbols.extend(DXY_COMPONENTS)

    _progress(progress, "BUILDING_SESSIONS", 32)
    _progress(progress, "BUILDING_ZONES", 42)
    _progress(progress, "CALCULATING_INDICATORS", 52)
    candidates: list[Candidate] = []
    rejected: list[dict[str, Any]] = []
    _progress(progress, "GENERATING_SETUPS", 62)
    for pair in config.instruments.pairs:
        pair_candidates, pair_rejected = _build_candidates(config, runtime, pair, raw[pair], dxy)
        candidates.extend(pair_candidates)
        rejected.extend(pair_rejected)

    _progress(progress, "SIMULATING_TRADES", 76)
    trades, risk_rejected = _apply_risk(config, runtime, candidates, raw)
    rejected.extend(risk_rejected)
    _progress(progress, "CALCULATING_STATISTICS", 88)
    analysis = build_analysis(trades, config)
    rejected_counts = dict(sorted(Counter(item["reason"] for item in rejected).items()))
    data_revision = (
        provider.data_revision(revision_symbols, start, end)
        if hasattr(provider, "data_revision")
        else None
    )
    result = {
        "generated_at": current.isoformat(),
        "name": config.name,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": config.timezone,
        "config_hash": config.config_hash,
        "strategy_version": config.strategy_version,
        "data_provider": getattr(provider, "name", runtime.data_provider),
        "data_revision": data_revision,
        "dxy_source": dxy_source,
        "execution_price_mode": config.execution.price_mode,
        "same_bar_policy": config.execution.same_bar_policy,
        "volume_mode": config.volume_mode,
        "candidate_count": len(candidates),
        "trade_count": len(trades),
        "rejected_count": len(rejected),
        "rejected_counts": rejected_counts,
        "summary": analysis["overall"],
        "analysis": {key: value for key, value in analysis.items() if key != "records"},
        "data_warnings": warnings,
        "candle_count": candle_count,
        "research_only": True,
    }
    return ResearchRunOutput(result, trades, rejected, analysis["records"], candle_count, warnings)
