from __future__ import annotations

from copy import deepcopy
from datetime import date

from app.research.analysis import build_analysis, configuration_diff, research_risk, trade_metrics
from app.research.config import StrategyConfig
from app.research.presets import builtin_presets
from app.research.windows import quick_period, walk_forward_windows


def sample_trades():
    return [
        {"trade_date": "2026-01-02", "entry_time": "2026-01-02T07:00:00Z", "exit_time": "2026-01-02T08:00:00Z", "pair": "EUR_USD", "direction": "long", "session": "BLUE", "zone_tf": "H2", "trigger_timeframe": "M1", "label_count": 2, "trade_number_day": 1, "spread_pips": 0.4, "outcome": "WIN", "r_multiple": 2.0, "equity_after": 10400.0},
        {"trade_date": "2026-02-03", "entry_time": "2026-02-03T12:00:00Z", "exit_time": "2026-02-03T13:00:00Z", "pair": "GBP_USD", "direction": "short", "session": "RED", "zone_tf": "H4", "trigger_timeframe": "M1", "label_count": 1, "trade_number_day": 1, "spread_pips": 1.2, "outcome": "LOSS", "r_multiple": -1.0, "equity_after": 10192.0},
    ]


def test_analysis_covers_periods_dimensions_and_drawdown():
    config = builtin_presets()[0]
    analysis = build_analysis(sample_trades(), config)
    assert analysis["overall"]["trades"] == 2
    assert analysis["overall"]["total_r"] == 1.0
    assert analysis["overall"]["max_drawdown_pct"] == 2.0
    assert {row["month"] for row in analysis["monthly_r"]} == {"2026-01", "2026-02"}
    assert any(row["dimension"] == "session" and row["bucket"] == "BLUE" for row in analysis["records"])


def test_oos_split_and_walk_forward_windows_are_chronological():
    payload = deepcopy(builtin_presets()[0].model_dump(mode="json"))
    payload["period"] = {"start": "2026-01-01", "end": "2026-02-28"}
    payload["oos"] = {
        "enabled": True,
        "training": {"start": "2026-01-01", "end": "2026-01-31"},
        "testing": {"start": "2026-02-01", "end": "2026-02-28"},
    }
    config = StrategyConfig.model_validate(payload)
    analysis = build_analysis(sample_trades(), config)
    assert analysis["scope_metrics"]["IS"]["trades"] == 1
    assert analysis["scope_metrics"]["OOS"]["trades"] == 1

    windows = walk_forward_windows(date(2024, 1, 1), date(2026, 12, 31), 12, 3, 3)
    assert windows[0]["training"]["end"] < windows[0]["testing"]["start"]
    assert windows[1]["training"]["start"] == date(2024, 4, 1)


def test_comparison_quick_period_and_overfitting_guard():
    first = builtin_presets()[0].model_dump(mode="json")
    second = builtin_presets()[1].model_dump(mode="json")
    differences = configuration_diff([first, second])
    assert "zones.timeframes" in differences
    assert quick_period(date(2026, 8, 25), "1m")[1] == date(2026, 8, 25)
    risk = research_risk(80, 15, 30, {"total_r": 20}, {"total_r": 2}, 0.2)
    assert risk["level"] == "HIGH"
