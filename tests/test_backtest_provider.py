from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.backtest import Candidate, _entry_price, run_backtest, simulate_trade
from app.config import Settings
from app.data.dukascopy import DUKASCOPY_SYMBOLS, DukascopyProvider, combine_bid_ask


def market_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    base = 1.1 + np.sin(np.arange(len(index)) / 10) * 0.0002
    bid = pd.DataFrame(
        {"open": base, "high": base + 0.0001, "low": base - 0.0001, "close": base, "volume": 1.0},
        index=index,
    )
    ask = bid.copy()
    ask[["open", "high", "low", "close"]] += 0.0002
    ask["volume"] = 2.0
    return combine_bid_ask(bid, ask)


class FakeProvider:
    name = "dukascopy"

    def __init__(self, frame: pd.DataFrame):
        self.frame = frame
        self.calls = []

    def get(self, instrument, start, end, interval="1min"):
        self.calls.append((instrument, pd.Timestamp(start), pd.Timestamp(end), interval))
        return self.frame.loc[(self.frame.index >= pd.Timestamp(start)) & (self.frame.index < pd.Timestamp(end))].copy()


def test_backtest_smoke_accepts_common_provider(tmp_path: Path):
    index = pd.date_range("2024-01-02T00:00:00Z", periods=180, freq="1min")
    provider = FakeProvider(market_frame(index))
    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "result.sqlite3",
        backtest_start="2024-01-02T00:00:00+00:00",
        backtest_end="2024-01-02T03:00:00+00:00",
        enable_scheduler=False,
        auto_run_on_startup=False,
    )
    result = run_backtest(settings, provider=provider, now=pd.Timestamp("2024-01-03T00:00:00Z"))
    assert result["data_provider"] == "dukascopy"
    assert result["dxy_source"] == "dukascopy_direct"
    assert len(result["summary"]) == 4
    assert {call[0] for call in provider.calls} == {"EUR_USD", "GBP_USD", "DXY"}


def test_backtest_smoke_runs_from_offline_parquet_cache(tmp_path: Path):
    index = pd.date_range("2024-01-02T00:00:00Z", periods=180, freq="1min")
    frame = market_frame(index)
    provider = DukascopyProvider(tmp_path, validation_mode="strict")
    generated_at = pd.Timestamp("2024-01-03T00:00:00Z")
    for symbol in ("EUR_USD", "GBP_USD", "DXY"):
        provider.store.write_day(
            symbol,
            index[0].date(),
            frame,
            DUKASCOPY_SYMBOLS[symbol],
            complete=True,
            generated_at=generated_at,
        )

    settings = Settings(
        data_dir=tmp_path,
        db_path=tmp_path / "result.sqlite3",
        backtest_start="2024-01-02T00:00:00+00:00",
        backtest_end="2024-01-02T03:00:00+00:00",
        enable_scheduler=False,
        auto_run_on_startup=False,
    )
    result = run_backtest(settings, provider=provider, now=generated_at)

    assert result["data_provider"] == "dukascopy"
    assert result["dxy_source"] == "dukascopy_direct"
    assert result["end"] == "2024-01-02T03:00:00+00:00"


def test_bid_ask_entry_and_exit_use_executable_sides(tmp_path: Path):
    index = pd.date_range("2024-01-02T12:00:00Z", periods=2, freq="1min")
    raw = market_frame(index)
    raw.loc[index[1], "bid_h"] = 1.1033
    raw.loc[index[1], "mid_h"] = (raw.loc[index[1], "bid_h"] + raw.loc[index[1], "ask_h"]) / 2
    entry, spread, mode = _entry_price(raw, index[0], "long", "bid_ask", 0)
    assert entry == pytest.approx(raw.loc[index[0], "ask_o"])
    assert spread == pytest.approx(2.0)
    assert mode == "bid_ask"

    candidate = Candidate(
        "A.0",
        "EUR_USD",
        "long",
        "2024-01-02",
        "BLUE",
        "ASIA",
        index[0].isoformat(),
        index[0].isoformat(),
        1,
        index[0].isoformat(),
        entry,
        "H2",
        1.09,
        1.11,
        index[0].isoformat(),
        100.0,
        99.0,
        1.11,
        1.09,
        mode,
        spread,
    )
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "result.sqlite3")
    trade = simulate_trade(settings, candidate, raw)
    assert trade["outcome"] == "WIN"
    assert trade["entry_price"] == pytest.approx(raw.loc[index[0], "ask_o"])
