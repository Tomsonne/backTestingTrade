from __future__ import annotations

from copy import deepcopy
from datetime import date

import numpy as np
import pandas as pd

from app.backtest import Candidate, _dxy_nonconfirm, simulate_trade
from app.config import SessionDef, Settings
from app.data.dukascopy import combine_bid_ask
from app.research.config import StrategyConfig
from app.research.engine import _apply_risk, _breaks_configurable, _previous_session, runtime_settings
from app.research.presets import builtin_presets
from app.sessions import SessionInstance, build_session_instances
from app.zones import build_zones, find_active_zone


def canonical(index: pd.DatetimeIndex, price: float = 1.1) -> pd.DataFrame:
    bid = pd.DataFrame(
        {"open": price, "high": price + 0.0002, "low": price - 0.0002, "close": price, "volume": 1},
        index=index,
    )
    ask = bid.copy()
    ask[["open", "high", "low", "close"]] += 0.0002
    return combine_bid_ask(bid, ask)


def v2_config() -> StrategyConfig:
    payload = deepcopy(builtin_presets()[0].model_dump(mode="json"))
    payload.update(name="Rules", preset_name=None, strategy_version="riseup-v2")
    return StrategyConfig.model_validate(payload)


def candidate(at: pd.Timestamp, trade_date: str | None = None) -> Candidate:
    return Candidate(
        "Rules", "EUR_USD", "long", trade_date or at.date().isoformat(), "BLUE", "ASIA",
        at.isoformat(), at.isoformat(), 1, at.isoformat(), 1.1002, "H2", 1.09, 1.11,
        at.isoformat(), 100, 99, 1.11, 1.09, "bid_ask", 2.0,
    )


def test_overnight_session_uses_end_date_as_trade_date():
    sessions = build_session_instances(
        pd.Timestamp("2024-01-01T00:00:00Z").to_pydatetime(),
        pd.Timestamp("2024-01-03T00:00:00Z").to_pydatetime(),
        [SessionDef("ASIA", pd.Timestamp("23:00").time(), pd.Timestamp("06:00").time())],
        "Europe/Paris",
    )
    selected = next(item for item in sessions if item.start == pd.Timestamp("2024-01-01T22:00:00Z"))
    assert selected.end == pd.Timestamp("2024-01-02T05:00:00Z")
    assert selected.trade_date == date(2024, 1, 2)


def test_each_session_can_use_its_own_dst_timezone():
    sessions = build_session_instances(
        pd.Timestamp("2024-03-29T00:00:00Z").to_pydatetime(),
        pd.Timestamp("2024-04-02T00:00:00Z").to_pydatetime(),
        [SessionDef("LONDON", pd.Timestamp("07:00").time(), pd.Timestamp("08:00").time(), "Europe/Paris")],
        "UTC",
    )
    before = next(item for item in sessions if item.trade_date == date(2024, 3, 29))
    after = next(item for item in sessions if item.trade_date == date(2024, 4, 1))
    assert before.start.hour == 6
    assert after.start.hour == 5


def test_previous_session_can_be_chronological_or_named():
    base = pd.Timestamp("2024-01-02T00:00:00Z")
    sessions = [
        SessionInstance("ASIA", base, base + pd.Timedelta(hours=6), date(2024, 1, 2)),
        SessionInstance("BLUE", base + pd.Timedelta(hours=7), base + pd.Timedelta(hours=11), date(2024, 1, 2)),
        SessionInstance("RED", base + pd.Timedelta(hours=12), base + pd.Timedelta(hours=16), date(2024, 1, 2)),
    ]
    config = v2_config()
    assert _previous_session(sessions, 2, config).name == "BLUE"
    config.divergence.previous_session = "named"
    config.divergence.previous_session_name = "ASIA"
    assert _previous_session(sessions, 2, config).name == "ASIA"


def test_break_detection_respects_direction_and_tolerance():
    index = pd.date_range("2024-01-02T07:00:00Z", periods=3, freq="1min")
    frame = canonical(index)
    frame.loc[index[1], "mid_h"] = 1.1012
    current = SessionInstance("BLUE", index[0], index[-1] + pd.Timedelta(minutes=1), date(2024, 1, 2))
    config = v2_config()
    config.divergence.tolerance_pips = 1.0
    breaks = _breaks_configurable(frame, current, 1.1010, 1.09, config)
    assert breaks == {"short": index[1]}


def test_dxy_nonconfirmation_never_reads_after_cutoff():
    index = pd.date_range("2024-01-02T07:00:00Z", periods=4, freq="1min")
    dxy = pd.DataFrame({"open": 100, "high": [100, 100, 102, 103], "low": 99, "close": 100}, index=index)
    current = SessionInstance("BLUE", index[0], index[-1] + pd.Timedelta(minutes=1), date(2024, 1, 2))
    assert _dxy_nonconfirm(dxy, current, index[2], "long", 101, 98)
    assert not _dxy_nonconfirm(dxy, current, index[3], "long", 101, 98)


def test_same_bar_policy_is_explicit_and_deterministic(tmp_path):
    index = pd.date_range("2024-01-02T07:00:00Z", periods=1, freq="1min")
    frame = canonical(index)
    frame.loc[index[0], ["bid_l", "bid_h"]] = [1.0980, 1.1040]
    item = candidate(index[0])
    stop_first = Settings(data_dir=tmp_path, db_path=tmp_path / "x.sqlite3", same_bar_policy="stop_first")
    tp_first = Settings(data_dir=tmp_path, db_path=tmp_path / "x.sqlite3", same_bar_policy="tp_first")
    assert simulate_trade(stop_first, item, frame)["outcome"] == "LOSS"
    assert simulate_trade(tp_first, item, frame)["outcome"] == "WIN"


def test_daily_trade_limit_and_one_position_rule(tmp_path):
    index = pd.date_range("2024-01-02T07:00:00Z", periods=1500, freq="1min")
    frame = canonical(index)
    config = v2_config()
    config.risk.max_trades_per_day = 2
    config.risk.stop_after_win = False
    config.risk.second_trade_after_loss = True
    runtime = runtime_settings(Settings(data_dir=tmp_path, db_path=tmp_path / "x.sqlite3"), config)
    first = candidate(index[0])
    second = candidate(index[1])
    third = candidate(index[2])
    trades, rejected = _apply_risk(config, runtime, [first, second, third], {"EUR_USD": frame})
    assert len(trades) == 1
    assert any(item["reason"] == "POSITION_ALREADY_OPEN" for item in rejected)

    next_day = candidate(index[1000], "2024-01-03")
    trades, rejected = _apply_risk(config, runtime, [first, next_day], {"EUR_USD": frame})
    assert len(trades) == 1
    assert rejected[0]["reason"] == "POSITION_ALREADY_OPEN"

    fast_exit = frame.copy()
    fast_exit["bid_l"] = 1.0
    trades, rejected = _apply_risk(
        config, runtime, [first, second, third], {"EUR_USD": fast_exit}
    )
    assert len(trades) == 2
    assert rejected[-1]["reason"] == "DAILY_LIMIT_REACHED"


def test_htf_zone_is_unavailable_until_source_candle_closes(monkeypatch):
    index = pd.date_range("2024-01-02T00:00:00Z", periods=180, freq="1min")
    frame = canonical(index, 1.2)
    frame.loc[index[:60], ["mid_o", "mid_h", "mid_l", "mid_c"]] = [1.20, 1.201, 1.199, 1.2005]
    frame.loc[index[60:120], ["mid_o", "mid_h", "mid_l", "mid_c"]] = [1.15, 1.151, 1.139, 1.14]
    frame.loc[index[120:180], ["mid_o", "mid_h", "mid_l", "mid_c"]] = [1.10, 1.101, 1.099, 1.10]
    day = frame.tz_convert("Europe/Paris").index.normalize()[0]
    monkeypatch.setattr("app.zones.daily_adr_series", lambda *args, **kwargs: pd.Series([1.0], index=[day]))
    zones = build_zones(frame, "H1", "Europe/Paris", min_adr_pct=0.1)
    assert zones
    zone = zones[0]
    assert zone.formed_at == pd.Timestamp("2024-01-02T03:00:00Z")
    assert find_active_zone([zone], zone.formed_at - pd.Timedelta(minutes=1), zone.bottom, "short") is None
