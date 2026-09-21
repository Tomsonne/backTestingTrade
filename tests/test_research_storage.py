from __future__ import annotations

from contextlib import closing
from pathlib import Path
import sqlite3

import pytest

from app.research.presets import builtin_presets
from app.storage import (
    create_preset,
    create_run,
    delete_preset,
    get_run,
    get_stats,
    init_db,
    list_presets,
    list_rejected,
    list_runs,
    list_trades,
    save_run_output,
    seed_presets,
    update_run_progress,
)


@pytest.mark.parametrize("existing_state", [False, True])
def test_init_db_rolls_back_partial_schema_on_ddl_failure(tmp_path: Path, monkeypatch, existing_state):
    database = tmp_path / "migration.sqlite3"
    schema_query = "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
    with closing(sqlite3.connect(database)) as connection:
        if existing_state:
            connection.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute("INSERT INTO state VALUES ('latest', 'historical result')")
            connection.commit()
        original_schema = connection.execute(schema_query).fetchall()

    real_connect = sqlite3.connect
    attempted_tables = []

    def fail_middle_ddl(action, name, *unused):
        if action == sqlite3.SQLITE_CREATE_TABLE:
            attempted_tables.append(name)
            if name == "trade_data_quality":
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def failing_connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs)
        connection.set_authorizer(fail_middle_ddl)
        return connection

    with monkeypatch.context() as patch:
        patch.setattr("app.storage.sqlite3.connect", failing_connect)
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            init_db(database)

    assert "run_data_gaps" in attempted_tables
    assert attempted_tables[-1] == "trade_data_quality"
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(schema_query).fetchall() == original_schema
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        if existing_state:
            assert connection.execute("SELECT * FROM state").fetchall() == [("latest", "historical result")]

    # A failed migration can be retried, then repeated without changing the schema.
    init_db(database)
    with closing(sqlite3.connect(database)) as connection:
        migrated_schema = connection.execute(schema_query).fetchall()
    init_db(database)
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute(schema_query).fetchall() == migrated_schema
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        if existing_state:
            assert connection.execute("SELECT * FROM state").fetchall() == [("latest", "historical result")]


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
