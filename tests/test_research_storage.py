from __future__ import annotations

from pathlib import Path

import pytest

from app.research.presets import builtin_presets
from app.storage import (
    create_preset,
    create_run,
    delete_preset,
    get_run,
    get_stats,
    list_presets,
    list_rejected,
    list_runs,
    list_trades,
    save_run_output,
    seed_presets,
    update_run_progress,
)


def test_preset_crud_preserves_builtins(tmp_path: Path):
    database = tmp_path / "lab.sqlite3"
    builtins = builtin_presets()
    seed_presets(database, builtins)
    assert len(list_presets(database)) == 4

    config = builtins[0].model_copy(update={"name": "My experiment", "preset_name": None})
    custom = create_preset(database, config.name, config.model_dump(mode="json"), config.config_hash)
    assert custom["built_in"] is False
    assert len(list_presets(database)) == 5
    assert delete_preset(database, custom["id"])
    with pytest.raises(ValueError):
        delete_preset(database, "builtin:Legacy A.0")


def test_run_history_trades_rejections_and_stats_are_persistent(tmp_path: Path):
    database = tmp_path / "lab.sqlite3"
    config = builtin_presets()[0]
    payload = config.model_dump(mode="json")
    run_id = create_run(database, payload, config.config_hash, "dukascopy", "abc123", "2.0.0")
    update_run_progress(database, run_id, "LOADING_DATA", 10)
    save_run_output(
        database,
        run_id,
        {"summary": {"trades": 1}},
        [
            {
                "pair": "EUR_USD",
                "direction": "long",
                "trade_date": "2026-01-02",
                "session": "BLUE",
                "entry_time": "2026-01-02T07:00:00Z",
                "exit_time": "2026-01-02T08:00:00Z",
                "outcome": "WIN",
                "r_multiple": 2.0,
                "dxy_prev_high": float("nan"),
            }
        ],
        [
            {
                "pair": "GBP_USD",
                "direction": "short",
                "trade_date": "2026-01-02",
                "session": "RED",
                "reason": "NO_ZONE",
                "trace": [{"condition": "HTF zone", "status": "FAIL"}],
            }
        ],
        [{"scope": "period", "dimension": "month", "bucket": "2026-01", "metrics": {"trades": 1}}],
        candle_count=1234,
        data_warnings=[],
    )
    update_run_progress(database, run_id, "COMPLETED", 100)

    assert len(list_runs(database)) == 1
    run = get_run(database, run_id)
    assert run and run["status"] == "COMPLETED" and run["candle_count"] == 1234
    stored_trade = list_trades(database, run_id)[0]
    assert stored_trade["pair"] == "EUR_USD"
    assert stored_trade["dxy_prev_high"] is None
    assert list_rejected(database, run_id, reason="NO_ZONE")[0]["pair"] == "GBP_USD"
    assert list_rejected(database, run_id, date_from="2026-01-03") == []
    assert get_stats(database, run_id)[0]["metrics"]["trades"] == 1
