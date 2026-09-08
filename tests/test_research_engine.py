from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd

from app.config import Settings
from app.data.dukascopy import combine_bid_ask
from app.m1_divergence import _ph, pivot_available_index
from app.research.config import StrategyConfig
from app.research.engine import run_research_backtest
from app.research.presets import builtin_presets


def canonical_prices(index: pd.DatetimeIndex, price: float = 1.1) -> pd.DataFrame:
    bid = pd.DataFrame(
        {
            "open": price,
            "high": price + 0.0001,
            "low": price - 0.0001,
            "close": price,
            "volume": 1.0,
        },
        index=index,
    )
    ask = bid.copy()
    ask[["open", "high", "low", "close"]] += 0.0002
    return combine_bid_ask(bid, ask)


class LocalProvider:
    name = "dukascopy"

    def __init__(self, frame: pd.DataFrame):
        self.frame = frame

    def get(self, instrument, start, end, interval="1min"):
        return self.frame.loc[(self.frame.index >= pd.Timestamp(start)) & (self.frame.index < pd.Timestamp(end))]


def custom_config() -> StrategyConfig:
    payload = deepcopy(builtin_presets()[0].model_dump(mode="json"))
    payload.update(name="Trace test", preset_name=None, strategy_version="riseup-v2")
    payload["period"] = {"start": "2024-01-02", "end": "2024-01-02"}
    payload["instruments"]["pairs"] = ["EUR_USD"]
    payload["divergence"]["enabled"] = False
    payload["zones"]["timeframes"] = []
    payload["trigger"]["mode"] = "NONE"
    payload["trigger"]["timeframes"] = []
    payload["validation"]["mode"] = "strict"
    return StrategyConfig.model_validate(payload)


def test_research_engine_records_rejected_setup_reason(tmp_path: Path):
    index = pd.date_range("2024-01-01T22:00:00Z", "2024-01-03T00:00:00Z", freq="1min", inclusive="left")
    provider = LocalProvider(canonical_prices(index))
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "lab.sqlite3")
    progress = []

    output = run_research_backtest(
        custom_config(), settings, provider=provider,
        now=pd.Timestamp("2024-01-04T00:00:00Z"),
        progress=lambda step, pct: progress.append((step, pct)),
    )

    assert output.result["trade_count"] == 0
    assert output.result["rejected_counts"]["NO_PAIR_BREAK"] > 0
    no_break = next(item for item in output.rejected if item["reason"] == "NO_PAIR_BREAK")
    assert no_break["trace"][0]["status"] == "PASS"
    assert progress[0][0] == "LOADING_DATA"
    assert progress[-1][0] == "CALCULATING_STATISTICS"


def test_pivot_right_bars_define_real_information_availability():
    values = np.array([1, 2, 3, 4, 5, 10, 5, 4, 3, 2, 1], dtype=float)
    assert _ph(values, 5, 3, 3)
    assert pivot_available_index(5, 3) == 8
    assert pivot_available_index(5, 3) + 1 == 9


def test_legacy_preset_routes_to_exact_engine_and_filters_variant(monkeypatch, tmp_path: Path):
    trades = []
    summaries = []
    for index, variant in enumerate(("A.0", "A.1", "B.0", "B.1")):
        trades.append(
            {
                "variant": variant, "pair": "EUR_USD", "direction": "long",
                "trade_date": "2026-02-02", "session": "BLUE",
                "entry_time": f"2026-02-02T{index + 7:02d}:00:00Z",
                "exit_time": f"2026-02-02T{index + 7:02d}:30:00Z",
                "outcome": "WIN", "r_multiple": 2.0, "return_pct": 2.0,
                "equity_after": 10200,
            }
        )
        summaries.append(
            {"variant": variant, "trades": 1, "wins": 1, "losses": 0, "total_r": 2.0}
        )
    monkeypatch.setattr(
        "app.research.engine.run_backtest",
        lambda *args, **kwargs: {
            "trades": trades, "summary": summaries, "candidate_count": 4,
            "candidate_counts": {variant: 1 for variant in ("A.0", "A.1", "B.0", "B.1")},
            "candle_count": 100, "generated_at": "2026-02-03T00:00:00Z",
        },
    )
    output = run_research_backtest(
        builtin_presets()[2],
        Settings(data_dir=tmp_path, db_path=tmp_path / "lab.sqlite3"),
    )
    assert [trade["variant"] for trade in output.trades] == ["B.0"]
    assert output.result["selected_variant"] == "B.0"
    assert output.result["candidate_count"] == 1
    assert [trade["variant"] for trade in output.result["trades"]] == ["B.0"]
    assert output.result["summary"]["total_r"] == 2.0
    assert output.trades[0]["trace"][0]["condition"] == "Legacy engine"
