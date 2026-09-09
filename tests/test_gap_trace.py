from copy import deepcopy
from dataclasses import asdict
from datetime import date

import pandas as pd
import pytest

from app.backtest import simulate_trade, _simulate_observed_trade
from app.config import Settings
from app.data.gaps import GapCatalog, MINUTE, merge_events
from app.research.analysis import build_analysis, trade_metrics
from app.research.data_quality import setup_events, quality_report
from app.research.engine import run_research_backtest
from app.sessions import SessionInstance
from test_strategy_rules import canonical, candidate
from test_research_engine import custom_config, LocalProvider


def scenario(tmp_path, direction="long", reopening=1.1002, gaps=(1, 2), periods=7):
    index = pd.date_range("2024-01-02T07:00:00Z", periods=periods, freq="min")
    frame = canonical(index, 1.1000)
    item = candidate(index[0])
    item.direction = direction
    # Make executable side symmetric around the same entry for both directions.
    side = "bid" if direction == "long" else "ask"
    for suffix in ("o", "h", "l", "c"):
        frame[f"{side}_{suffix}"] = 1.1002
    after = max(gaps) + 1
    if after < periods:
        frame.loc[index[after], [f"{side}_{s}" for s in ("o", "h", "l", "c")]] = reopening
    frame = frame.drop(index[list(gaps)])
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "test.db", data_validation_mode="trace", max_hold_minutes=periods-1)
    settings.gap_catalog = GapCatalog(index[0], index[-1] + MINUTE)
    settings.gap_catalog.add("EUR_USD", frame)
    return settings, item, frame, index


@pytest.mark.parametrize("direction,price,outcome,source", [
    ("long", 1.1040, "WIN", "GAP_INFERRED_TP"),
    ("long", 1.0980, "LOSS", "GAP_INFERRED_SL"),
    ("short", 1.0960, "WIN", "GAP_INFERRED_TP"),
    ("short", 1.1020, "LOSS", "GAP_INFERRED_SL"),
    ("long", 1.1002, "TIMEOUT", "GAP_ASSUMED_NO_EXIT"),
    ("short", 1.1002, "TIMEOUT", "GAP_ASSUMED_NO_EXIT"),
])
def should_resolve_using_executable_open_when_trade_crosses_gap(tmp_path, direction, price, outcome, source):
    settings, item, frame, index = scenario(tmp_path, direction, price)
    before = frame.copy(deep=True)
    result = simulate_trade(settings, item, frame)
    assert result["outcome"] == outcome
    assert result["outcome_source"] == source
    assert result["data_quality_status"] == "GAP_RESOLVED"
    event, = result["missing_data_events"]
    assert event["gap_start"] == index[1].isoformat()
    assert event["gap_end"] == index[2].isoformat()
    assert event["missing_count"] == result["missing_candles_count"] == 2
    assert event["first_available_timestamp"] == index[3].isoformat()
    assert event["resolution_method"] == "NEXT_AVAILABLE_CANDLE"
    assert event["details"]["executable_side"] == ("bid" if direction == "long" else "ask")
    if outcome != "TIMEOUT":
        assert result["exit_time"] == index[3].isoformat()
    pd.testing.assert_frame_equal(frame, before)


@pytest.mark.parametrize("policy", ["stop_first", "tp_first"])
def should_prioritize_open_over_ohlc_when_gap_reopens_beyond_tp(tmp_path, policy):
    settings, item, frame, index = scenario(tmp_path, reopening=1.1040)
    settings.same_bar_policy = policy
    frame.loc[index[3], "bid_l"] = 1.09
    result = simulate_trade(settings, item, frame)
    assert result["outcome_source"] == "GAP_INFERRED_TP"


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("policy,outcome", [("stop_first", "LOSS"), ("tp_first", "WIN")])
def should_apply_normal_same_bar_policy_when_reopening_is_between_levels(tmp_path, direction, policy, outcome):
    settings, item, frame, index = scenario(tmp_path, direction)
    settings.same_bar_policy = policy
    side = "bid" if direction == "long" else "ask"
    frame.loc[index[3], [f"{side}_h", f"{side}_l"]] = [1.11, 1.09]
    result = simulate_trade(settings, item, frame)
    assert result["outcome"] == outcome
    assert result["outcome_source"] == "GAP_ASSUMED_NO_EXIT"
    assert result["exit_observation_source"] == "OBSERVED"
    assert result["missing_data_events"][0]["gap_resolution"] == "ASSUMED_NO_EXIT"


def should_preserve_all_gap_events_when_position_spans_multiple_gaps(tmp_path):
    settings, item, frame, index = scenario(tmp_path, gaps=(1, 2, 4))
    result = simulate_trade(settings, item, frame)
    assert result["missing_gap_count"] == 2
    assert result["missing_candles_count"] == 3
    assert [e["first_available_timestamp"] for e in result["missing_data_events"]] == [index[3].isoformat(), index[5].isoformat()]


def should_not_count_later_gap_when_first_gap_infers_exit(tmp_path):
    settings, item, frame, index = scenario(tmp_path, gaps=(1, 2, 4))
    frame.loc[index[3], ["bid_o", "bid_h", "bid_l", "bid_c"]] = 1.104
    result = simulate_trade(settings, item, frame)
    assert result["missing_gap_count"] == 1


def should_leave_pnl_unknown_when_no_next_candle_exists(tmp_path):
    settings, item, frame, index = scenario(tmp_path, gaps=(4, 5, 6))
    result = simulate_trade(settings, item, frame)
    assert result["data_quality_status"] == result["outcome"] == "INDETERMINATE"
    assert result["r_multiple"] is result["exit_time"] is result["exit_price"] is None
    assert result["missing_data_events"][0]["gap_resolution"] == "UNRESOLVED"
    assert trade_metrics([result], 10000)["trades"] == 0


def should_not_see_future_candle_when_it_is_after_holding_horizon(tmp_path):
    settings, item, frame, index = scenario(tmp_path)
    settings.max_hold_minutes = 2
    assert simulate_trade(settings, item, frame)["outcome"] == "INDETERMINATE"


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("mode", ["bid_ask", "mid"])
@pytest.mark.parametrize("policy", ["stop_first", "tp_first"])
def should_preserve_every_economic_field_when_no_gap_exists(tmp_path, direction, mode, policy):
    index = pd.date_range("2024-01-02T07:00:00Z", periods=9, freq="min")
    frame = canonical(index)
    frame.loc[index[5], ["bid_h", "ask_h", "mid_h"]] = 1.12
    frame.loc[index[5], ["bid_l", "ask_l", "mid_l"]] = 1.08
    item = candidate(index[0]); item.direction = direction; item.execution_price_mode = mode
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "x.db", data_validation_mode="trace", same_bar_policy=policy)
    observed = _simulate_observed_trade(settings, item, frame)
    traced = simulate_trade(settings, item, frame)
    metadata = {"data_quality_status", "missing_data_events"}
    assert {k: traced[k] for k in observed if k not in metadata} == {k: v for k, v in observed.items() if k not in metadata}
    assert traced["data_quality_status"] == "COMPLETE"


def context_fixture():
    index = pd.date_range("2024-01-02T00:00:00Z", periods=12*60, freq="min")
    frame = canonical(index).drop(index[[30, 60, 180, 300, 540, 600]])
    catalog = GapCatalog(index[0], index[-1]+MINUTE)
    catalog.add("EUR_USD", frame)
    previous = SessionInstance("PREV", index[60], index[240], index[0].date())
    current = SessionInstance("CUR", index[300], index[540], index[0].date())
    return catalog, previous, current, index, frame


def should_not_contaminate_setup_when_gap_is_outside_used_context():
    catalog, previous, current, index, _ = context_fixture()
    events = setup_events(catalog, "EUR_USD", previous, current, index[400])
    assert {e["gap_start"] for e in events} == {index[i].isoformat() for i in (60, 180, 300)}
    opening = next(e for e in events if e["gap_start"] == index[60].isoformat())
    assert set(opening["affected_components"]) == {"SESSION_OPEN", "SESSION_LIQUIDITY"}
    assert sum(e["missing_count"] for e in events) == 3


def should_trace_dxy_edges_and_previous_session_when_dxy_is_required():
    catalog, previous, current, index, frame = context_fixture()
    catalog.add("DXY", frame)
    catalog.dxy_symbols = ["DXY"]
    events = setup_events(catalog, "EUR_USD", previous, current, index[400], dxy=True)
    dxy = [e for e in events if e["symbol"] == "DXY"]
    assert {e["gap_start"] for e in dxy} == {index[i].isoformat() for i in (60, 180, 300)}
    assert all(e["affected_components"] == ["DXY_DIVERGENCE"] for e in dxy)


def should_trace_recursive_dependencies_when_trigger_and_zones_are_enabled():
    catalog, previous, current, index, frame = context_fixture()
    events = setup_events(catalog, "EUR_USD", previous, current, index[400], trigger=True, zones=True,
                          label_time=index[299], history_index=frame.index)
    first = next(e for e in events if e["gap_start"] == index[30].isoformat())
    assert set(first["affected_components"]) == {"HTF_ZONE", "TRIGGER", "INDICATOR", "WARMUP"}
    confirmation = next(e for e in events if e["gap_start"] == index[300].isoformat())
    assert "LABEL_CONFIRMATION" in confirmation["affected_components"]
    assert all(pd.Timestamp(e["gap_end"]) < index[400] for e in events)


def should_count_each_physical_gap_once_when_categories_and_trades_share_it():
    catalog, previous, current, index, _ = context_fixture()
    events = setup_events(catalog, "EUR_USD", previous, current, index[400])
    assert len(merge_events(events, events)) == len(events)
    trade = {"missing_data_events": events, "data_quality_status": "DEGRADED"}
    report = quality_report(catalog.physical_gaps(), [trade], [trade, trade])
    assert report["missing_candles"] == 6
    assert report["affected_trades"] == 2
    assert report["categories"]["SESSION_LIQUIDITY"] == 3


def should_separate_clean_metrics_when_other_trades_use_assumptions():
    config = custom_config(); config.validation.mode = "trace"
    def trade(status, r, ret):
        return dict(data_quality_status=status, outcome="INDETERMINATE" if r is None else "WIN" if r > 0 else "LOSS",
                    r_multiple=r, return_pct=ret, equity_after=12345, entry_time="2024-01-02T07:00:00Z",
                    exit_time=None if r is None else "2024-01-02T08:00:00Z", trade_date="2024-01-02")
    rows = [trade("COMPLETE", 2, 4), trade("DEGRADED", -1, -2),
            trade("GAP_RESOLVED", 2, 4), trade("INDETERMINATE", None, None)]
    compare = build_analysis(rows, config)["quality_comparison"]
    assert compare["ALL"]["trades"] == 3
    assert compare["ALL"]["total_r"] == 3
    assert compare["CLEAN"]["trades"] == 1
    assert compare["CLEAN"]["return_pct"] == pytest.approx(4)
    assert compare["indeterminate_count"] == 1


def should_continue_partial_session_when_trace_has_usable_levels(tmp_path):
    index = pd.date_range("2024-01-01T23:00:00Z", "2024-01-02T23:00:00Z", inclusive="left", freq="min")
    frame = canonical(index)
    # BLUE starts at 06:00 UTC. A real break and next real entry are present.
    frame.loc[index >= pd.Timestamp("2024-01-02T06:01Z"), "mid_h"] = 1.101
    frame.loc[index >= pd.Timestamp("2024-01-02T06:01Z"), "ask_h"] = 1.1011
    frame.loc[index >= pd.Timestamp("2024-01-02T06:01Z"), "bid_h"] = 1.1009
    frame = frame.drop(pd.Timestamp("2024-01-02T01:00Z"))
    config = custom_config(); config.validation.mode = "trace"
    config.period.start = date(2024, 1, 1)
    output = run_research_backtest(config, Settings(data_dir=tmp_path, db_path=tmp_path / "x.db"), LocalProvider(frame))
    assert output.trades
    assert output.trades[0]["data_quality_status"] in {"DEGRADED", "GAP_RESOLVED"}
    assert any("SESSION_LIQUIDITY" in e["affected_components"] for e in output.trades[0]["missing_data_events"])
    assert not any(r["reason"] == "PREVIOUS_SESSION_DATA_MISSING" for r in output.rejected)


def should_reject_corrupt_prices_when_trace_only_relaxes_missing_data():
    catalog, _, _, index, frame = context_fixture()
    frame.loc[index[1], "bid_l"] = 100
    with pytest.raises(ValueError, match="invalid OHLC"):
        catalog.add("EUR_USD", frame)


def should_ignore_scheduled_closures_when_cataloguing_missing_minutes():
    index = pd.date_range("2024-01-05T21:59Z", "2024-01-07T22:01Z", freq="min")
    frame = canonical(pd.DatetimeIndex([index[0], index[-2], index[-1]]))
    catalog = GapCatalog(index[0], index[-1]+MINUTE)
    catalog.add("EUR_USD", frame)
    assert catalog.physical_gaps() == []


def should_preserve_full_run_economics_when_trace_has_no_missing_minutes(tmp_path):
    index = pd.date_range("2024-01-01T23:00Z", "2024-01-02T23:00Z", inclusive="left", freq="min")
    frame = canonical(index)
    after = index >= pd.Timestamp("2024-01-02T12:01Z")
    for prefix in ("bid", "ask", "mid"):
        frame.loc[after, prefix + "_h"] = 1.102
    config = custom_config()
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "run.db")
    strict = run_research_backtest(config, settings, LocalProvider(frame))
    config.validation.mode = "trace"
    traced = run_research_backtest(config, settings, LocalProvider(frame))
    assert strict.trades and len(strict.trades) == len(traced.trades)
    for before, after in zip(strict.trades, traced.trades):
        for field in ("entry_time", "entry_price", "exit_time", "exit_price", "outcome", "r_multiple", "risk_pct", "return_pct", "equity_after"):
            assert before[field] == after[field]
    assert strict.result["summary"] == traced.result["summary"]
    assert all(t["data_quality_status"] == "COMPLETE" for t in traced.trades)


def should_keep_strict_blocking_when_trace_is_available(tmp_path):
    from app.data.dukascopy import DukascopyProvider
    from app.data.base import DataCoverageError
    settings, item, frame, index = scenario(tmp_path)
    provider = DukascopyProvider(tmp_path, validation_mode="strict")
    provider.store.read = lambda *args: frame
    provider._metadata = lambda *args, **kwargs: {}
    with pytest.raises(DataCoverageError, match="missing M1"):
        provider.get("EUR_USD", index[0], index[-1] + MINUTE)
    provider.validation_mode = "trace"
    pd.testing.assert_frame_equal(provider.get("EUR_USD", index[0], index[-1]+MINUTE), frame)


def should_record_both_missing_intervals_when_weekend_splits_one_unobserved_position(tmp_path):
    index = pd.DatetimeIndex([pd.Timestamp("2024-01-05T21:58Z"), pd.Timestamp("2024-01-07T22:01Z")])
    frame = canonical(index)
    frame.loc[index[1], ["bid_o", "bid_h", "bid_l", "bid_c"]] = 1.104
    item = candidate(index[0])
    settings = Settings(data_dir=tmp_path, db_path=tmp_path / "x.db", data_validation_mode="trace", max_hold_minutes=3000)
    result = simulate_trade(settings, item, frame)
    assert result["outcome_source"] == "GAP_INFERRED_TP"
    assert result["missing_candles_count"] == 2
    assert result["missing_gap_count"] == 2
    assert {e["first_available_timestamp"] for e in result["missing_data_events"]} == {index[1].isoformat()}


def should_include_mid_execution_when_gap_resolution_uses_legacy_prices(tmp_path):
    settings, item, frame, index = scenario(tmp_path)
    item.execution_price_mode = "mid"
    frame.loc[index[3], ["mid_o", "mid_h", "mid_l", "mid_c"]] = 1.104
    result = simulate_trade(settings, item, frame)
    assert result["outcome_source"] == "GAP_INFERRED_TP"
    assert result["missing_data_events"][0]["details"]["executable_side"] == "mid"


def should_apply_execution_costs_when_raw_reopening_is_not_executable_tp(tmp_path):
    settings, item, frame, index = scenario(tmp_path, reopening=1.10325)
    item.additional_spread_pips = 2
    item.slippage_pips = 1
    result = simulate_trade(settings, item, frame)
    assert result["missing_data_events"][0]["gap_resolution"] == "ASSUMED_NO_EXIT"
    assert result["missing_data_events"][0]["details"]["first_available_ohlc"]["open"] == pytest.approx(1.10305)


def should_continue_dxy_computation_when_trace_has_partial_dxy_context(tmp_path):
    index = pd.date_range("2024-01-01T23:00Z", "2024-01-02T23:00Z", inclusive="left", freq="min")
    frame = canonical(index)
    for prefix in ("bid", "ask", "mid"):
        frame.loc[index >= pd.Timestamp("2024-01-02T12:01Z"), prefix + "_h"] = 1.102
    dxy = canonical(index, 100).drop(pd.Timestamp("2024-01-02T12:00Z"))
    class Provider(LocalProvider):
        def get(self, instrument, start, end, interval="1min"):
            return dxy if instrument == "DXY" else frame
    config = custom_config(); config.validation.mode = "trace"; config.divergence.enabled = True
    config.divergence.dxy_gap_behavior = "reject"
    output = run_research_backtest(config, Settings(data_dir=tmp_path, db_path=tmp_path / "x.db"), Provider(frame))
    assert output.trades
    assert any(e["symbol"] == "DXY" and "DXY_DIVERGENCE" in e["affected_components"] for t in output.trades for e in t["missing_data_events"])
    assert not any(r["reason"] == "DXY_DATA_MISSING" for r in output.rejected)


def should_persist_normalized_events_and_exports_when_trace_run_is_saved(tmp_path):
    from fastapi.testclient import TestClient
    from app.main import create_app
    from app.storage import (create_run, save_run_output, list_trades, get_run, connect,
                             init_db, list_missing_data_events, update_run_progress)
    settings, item, frame, index = scenario(tmp_path, reopening=1.104)
    settings.enable_scheduler = settings.auto_run_on_startup = False
    trade = simulate_trade(settings, item, frame)
    trade.update(return_pct=4, equity_before=10000, equity_after=10400)
    config = custom_config(); config.validation.mode = "trace"
    analysis = build_analysis([trade], config)
    result = dict(summary=analysis["overall"], analysis=analysis,
                  data_quality_report=quality_report(settings.gap_catalog.physical_gaps(), [], [trade]))
    old_id = create_run(settings.db_path, config.model_dump(mode="json"), config.config_hash, "dukascopy", "test", "2.0.0")
    save_run_output(settings.db_path, old_id, {"marker": "historical"}, [], [], [], candle_count=0, data_warnings=[])
    run_id = create_run(settings.db_path, config.model_dump(mode="json"), config.config_hash, "dukascopy", "test", "2.1.0")
    save_run_output(settings.db_path, run_id, result, [trade], [], analysis["records"], candle_count=len(frame), data_warnings=[])
    update_run_progress(settings.db_path, run_id, "COMPLETED", 100)
    init_db(settings.db_path); init_db(settings.db_path)
    assert get_run(settings.db_path, old_id)["result"] == {"marker": "historical"}
    saved, = list_trades(settings.db_path, run_id)
    event, = list_missing_data_events(settings.db_path, run_id)
    assert event["trade_id"] == saved["trade_id"]
    assert event["first_available_timestamp"] == index[3].isoformat()
    with connect(settings.db_path) as connection:
        assert not connection.execute("PRAGMA foreign_key_check").fetchall()
        assert connection.execute("SELECT missing_candles_count FROM trade_data_quality WHERE run_id=?", (run_id,)).fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM run_data_gaps WHERE run_id=?", (run_id,)).fetchone()[0] == 1
    with TestClient(create_app(settings)) as client:
        report = client.get(f"/api/backtests/{run_id}/data-quality").json()
        assert report["comparison"]["ALL"]["trades"] == 1
        assert client.get(f"/api/backtests/{run_id}/trades?quality=CLEAN").json() == []
        assert client.get(f"/api/backtests/{run_id}/trades?quality=GAP_RESOLVED").json()[0]["outcome_source"] == "GAP_INFERRED_TP"
        assert client.get(f"/api/backtests/{run_id}/trades?quality=bad").status_code == 422
        for kind in ("gaps.csv", "quality.json", "trades.csv", "run.json"):
            response = client.get(f"/api/backtests/{run_id}/export/{kind}")
            assert response.status_code == 200
            assert "GAP_INFERRED_TP" in response.text
        assert client.get(f"/api/backtests/{old_id}/data-quality").json()["report"] is None
