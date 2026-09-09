from __future__ import annotations

from collections import defaultdict
from datetime import date
from statistics import median
from typing import Any, Callable

import pandas as pd

from .config import StrategyConfig


def _streak(values: list[bool]) -> int:
    longest = current = 0
    for value in values:
        current = current + 1 if value else 0
        longest = max(longest, current)
    return longest


def trade_metrics(trades: list[dict[str, Any]], starting_equity: float) -> dict[str, Any]:
    trades = [t for t in trades if t.get("outcome") != "INDETERMINATE" and t.get("data_quality_status") != "INDETERMINATE"]
    if not trades:
        return {
            "trades": 0, "wins": 0, "losses": 0, "timeouts": 0, "win_rate": None,
            "total_r": 0.0, "average_r": 0.0, "median_r": 0.0, "profit_factor": None,
            "expectancy": 0.0, "return_pct": 0.0, "max_drawdown_pct": 0.0,
            "max_consecutive_wins": 0, "max_consecutive_losses": 0,
        }
    ordered = sorted(trades, key=lambda item: (item.get("entry_time", ""), item.get("pair", "")))
    outcomes = [item.get("outcome") for item in ordered]
    values = [float(item.get("r_multiple", 0) or 0) for item in ordered]
    wins, losses = outcomes.count("WIN"), outcomes.count("LOSS")
    timeouts = outcomes.count("TIMEOUT")
    positive = sum(value for value in values if value > 0)
    negative = abs(sum(value for value in values if value < 0))
    equity_values = [starting_equity]
    if all(item.get("return_pct") is not None for item in ordered):
        for item in ordered:
            equity_values.append(equity_values[-1] * (1 + float(item["return_pct"]) / 100))
    else:
        equity_values.extend(
            float(item["equity_after"]) for item in ordered if item.get("equity_after") is not None
        )
    peak, max_drawdown = equity_values[0], 0.0
    for equity in equity_values:
        peak = max(peak, equity)
        if peak:
            max_drawdown = max(max_drawdown, (peak - equity) / peak * 100)
    final_equity = equity_values[-1]
    decided = wins + losses
    return {
        "trades": len(ordered),
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "win_rate": 100 * wins / decided if decided else None,
        "total_r": float(sum(values)),
        "average_r": float(sum(values) / len(values)),
        "median_r": float(median(values)),
        "profit_factor": positive / negative if negative else None,
        "expectancy": float(sum(values) / len(values)),
        "return_pct": 100 * (final_equity / starting_equity - 1),
        "max_drawdown_pct": float(max_drawdown),
        "max_consecutive_wins": _streak([value == "WIN" for value in outcomes]),
        "max_consecutive_losses": _streak([value == "LOSS" for value in outcomes]),
    }


def equity_curve(trades: list[dict[str, Any]], starting_equity: float) -> list[dict[str, Any]]:
    trades = [t for t in trades if t.get("outcome") != "INDETERMINATE"]
    curve = [{"timestamp": None, "equity": starting_equity}]
    for item in sorted(trades, key=lambda row: row.get("exit_time", "")):
        curve.append(
            {
                "timestamp": item.get("exit_time"),
                "equity": float(item.get("equity_after", curve[-1]["equity"])),
                "trade_id": item.get("trade_id"),
            }
        )
    return curve


def _period_bucket(value: str, frequency: str) -> str:
    timestamp = pd.Timestamp(value)
    if frequency == "day":
        return timestamp.strftime("%Y-%m-%d")
    if frequency == "week":
        iso = timestamp.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    if frequency == "month":
        return timestamp.strftime("%Y-%m")
    if frequency == "quarter":
        return f"{timestamp.year}-Q{timestamp.quarter}"
    return timestamp.strftime("%Y")


def _bucket_records(
    trades: list[dict[str, Any]],
    scope: str,
    dimension: str,
    key: Callable[[dict[str, Any]], str],
    starting_equity: float,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for trade in trades:
        groups[str(key(trade))].append(trade)
    return [
        {"scope": scope, "dimension": dimension, "bucket": bucket,
         "metrics": trade_metrics(items, starting_equity)}
        for bucket, items in sorted(groups.items())
    ]


def build_analysis(trades: list[dict[str, Any]], config: StrategyConfig) -> dict[str, Any]:
    equity = config.risk.starting_equity
    records: list[dict[str, Any]] = []
    records.append(
        {"scope": "ALL", "dimension": "overall", "bucket": "all",
         "metrics": trade_metrics(trades, equity)}
    )
    for frequency in ("day", "week", "month", "quarter", "year"):
        records.extend(
            _bucket_records(
                trades, "ALL", frequency,
                lambda item, value=frequency: _period_bucket(item["trade_date"], value), equity,
            )
        )
    dimensions: dict[str, Callable[[dict[str, Any]], str]] = {
        "pair": lambda item: item.get("pair", "UNKNOWN"),
        "direction": lambda item: item.get("direction", "UNKNOWN"),
        "session": lambda item: item.get("session", "UNKNOWN"),
        "weekday": lambda item: pd.Timestamp(item["trade_date"]).day_name(),
        "zone_timeframe": lambda item: item.get("zone_tf") or "NONE",
        "trigger_timeframe": lambda item: item.get("trigger_timeframe", "M1"),
        "preset": lambda item: config.preset_name or config.name,
        "label_count": lambda item: str(item.get("label_count", 0)),
        "trade_number": lambda item: str(item.get("trade_number_day", 1)),
        "entry_hour": lambda item: pd.Timestamp(item["entry_time"]).strftime("%H:00"),
        "spread_range": lambda item: _spread_bucket(float(item.get("spread_pips", 0) or 0)),
    }
    for dimension, key in dimensions.items():
        records.extend(_bucket_records(trades, "ALL", dimension, key, equity))

    scope_metrics: dict[str, Any] = {}
    if config.oos.enabled and config.oos.training and config.oos.testing:
        for scope, period in (("IS", config.oos.training), ("OOS", config.oos.testing)):
            selected = [
                item for item in trades
                if period.start <= pd.Timestamp(item["trade_date"]).date() <= period.end
            ]
            metrics = trade_metrics(selected, equity)
            scope_metrics[scope] = metrics
            records.append({"scope": scope, "dimension": "overall", "bucket": "all", "metrics": metrics})

    monthly = [
        {"month": item["bucket"], "total_r": item["metrics"]["total_r"],
         "trades": item["metrics"]["trades"]}
        for item in records if item["scope"] == "ALL" and item["dimension"] == "month"
    ]
    overall = records[0]["metrics"]
    quality_comparison = None
    if config.validation.mode == "trace":
        clean = [t for t in trades if t.get("data_quality_status") == "COMPLETE"]
        clean_metrics = trade_metrics(clean, equity)
        records.append({"scope": "CLEAN", "dimension": "overall", "bucket": "all", "metrics": clean_metrics})
        quality_comparison = {
            "ALL": overall, "CLEAN": clean_metrics,
            "indeterminate_count": sum(t.get("data_quality_status") == "INDETERMINATE" for t in trades),
            "total_trades": len(trades),
            "note": "CLEAN is a COMPLETE-only cohort, compounded from starting equity with original risk percentages; not a rerun of daily selection. INDETERMINATE PnL is excluded, not zero.",
        }
    return {
        "overall": overall,
        "equity_curve": equity_curve(trades, equity),
        "monthly_r": monthly,
        "scope_metrics": scope_metrics,
        "records": records,
        **({"quality_comparison": quality_comparison} if quality_comparison is not None else {}),
    }


def _spread_bucket(value: float) -> str:
    if value < 0.5:
        return "<0.5"
    if value < 1.0:
        return "0.5-1.0"
    if value < 2.0:
        return "1.0-2.0"
    return ">=2.0"


def configuration_diff(configs: list[dict[str, Any]]) -> dict[str, list[Any]]:
    flattened = [_flatten(config) for config in configs]
    keys = sorted(set().union(*(item.keys() for item in flattened)))
    return {
        key: [item.get(key) for item in flattened]
        for key in keys
        if len({_stable(value) for value in (item.get(key) for item in flattened)}) > 1
    }


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            output.update(_flatten(child, path))
        return output
    return {prefix: value}


def _stable(value: Any) -> str:
    return repr(value)


def research_risk(
    run_count: int,
    free_parameters: int,
    variants_tested: int,
    is_metrics: dict[str, Any] | None = None,
    oos_metrics: dict[str, Any] | None = None,
    stability: float | None = None,
) -> dict[str, str]:
    score, reasons = 0, []
    if run_count > 50:
        score += 2; reasons.append("many research runs")
    elif run_count > 15:
        score += 1; reasons.append("multiple research runs")
    if free_parameters > 12:
        score += 2; reasons.append("many free parameters")
    elif free_parameters > 6:
        score += 1; reasons.append("several free parameters")
    if variants_tested > 20:
        score += 2; reasons.append("many variants compared")
    elif variants_tested > 5:
        score += 1; reasons.append("several variants compared")
    if is_metrics and oos_metrics:
        is_r, oos_r = float(is_metrics.get("total_r", 0)), float(oos_metrics.get("total_r", 0))
        if is_r > 0 and oos_r < is_r * 0.5:
            score += 2; reasons.append("large IS/OOS degradation")
    else:
        score += 1; reasons.append("no OOS evidence")
    if stability is not None and stability < 0.4:
        score += 2; reasons.append("neighboring parameters are unstable")
    level = "HIGH" if score >= 6 else "MEDIUM" if score >= 3 else "LOW"
    return {
        "level": level,
        "explanation": "; ".join(reasons) or "limited research degrees of freedom",
        "disclaimer": "Research heuristic only; it does not predict future performance.",
    }
