from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from numbers import Real
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4


RUN_STATUSES = {
    "QUEUED", "LOADING_DATA", "VALIDATING_DATA", "BUILDING_SESSIONS",
    "BUILDING_ZONES", "CALCULATING_INDICATORS", "GENERATING_SETUPS",
    "SIMULATING_TRADES", "CALCULATING_STATISTICS", "SAVING_RESULTS",
    "COMPLETED", "FAILED",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_safe(value: Any) -> Any:
    if isinstance(value, Real) and not isinstance(value, bool) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    return value


def _json(value: Any) -> str:
    return json.dumps(
        _json_safe(value), ensure_ascii=False, sort_keys=True, default=str, allow_nan=False
    )


def _loads(value: str | None, default: Any = None) -> Any:
    return default if value in (None, "") else _json_safe(json.loads(value))


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(path: Path) -> None:
    with connect(path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS presets (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE COLLATE NOCASE,
                built_in INTEGER NOT NULL DEFAULT 0,
                config_hash TEXT NOT NULL,
                config_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS backtest_runs (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                progress_step TEXT NOT NULL,
                progress_pct REAL NOT NULL DEFAULT 0,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                launched_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT,
                duration_seconds REAL,
                preset_name TEXT,
                config_hash TEXT NOT NULL,
                config_json TEXT NOT NULL,
                provider TEXT NOT NULL,
                instruments_json TEXT NOT NULL,
                strategy_version TEXT NOT NULL,
                git_commit TEXT,
                app_version TEXT NOT NULL,
                execution_model TEXT NOT NULL,
                candle_count INTEGER NOT NULL DEFAULT 0,
                data_warnings_json TEXT NOT NULL DEFAULT '[]',
                error_message TEXT,
                error_traceback TEXT,
                result_json TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_backtest_runs_launched
                ON backtest_runs(launched_at DESC);
            CREATE INDEX IF NOT EXISTS idx_backtest_runs_status
                ON backtest_runs(status);
            CREATE TABLE IF NOT EXISTS trades (
                run_id TEXT NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
                trade_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                trade_date TEXT,
                pair TEXT,
                direction TEXT,
                session TEXT,
                previous_session TEXT,
                entry_time TEXT,
                exit_time TEXT,
                outcome TEXT,
                r_multiple REAL,
                return_pct REAL,
                equity_before REAL,
                equity_after REAL,
                zone_timeframe TEXT,
                trigger_timeframe TEXT,
                spread_pips REAL,
                data_json TEXT NOT NULL,
                PRIMARY KEY(run_id, trade_id)
            );
            CREATE INDEX IF NOT EXISTS idx_trades_run_sequence ON trades(run_id, sequence);
            CREATE INDEX IF NOT EXISTS idx_trades_dimensions
                ON trades(run_id, pair, direction, session, trade_date);
            CREATE TABLE IF NOT EXISTS rejected_setups (
                run_id TEXT NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
                setup_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                trade_date TEXT,
                pair TEXT,
                direction TEXT,
                session TEXT,
                reason TEXT NOT NULL,
                trace_json TEXT NOT NULL,
                data_json TEXT NOT NULL,
                PRIMARY KEY(run_id, setup_id)
            );
            CREATE INDEX IF NOT EXISTS idx_rejected_filters
                ON rejected_setups(run_id, reason, pair, session, direction, trade_date);
            CREATE TABLE IF NOT EXISTS run_stats (
                run_id TEXT NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
                scope TEXT NOT NULL,
                dimension TEXT NOT NULL,
                bucket TEXT NOT NULL,
                metrics_json TEXT NOT NULL,
                PRIMARY KEY(run_id, scope, dimension, bucket)
            );
            """
        )


def save_result(path: Path, result: dict[str, Any]) -> None:
    """Compatibility store for the historical /api/result endpoint."""
    init_db(path)
    with connect(path) as connection:
        connection.execute(
            "INSERT INTO state(key,value) VALUES('latest',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (_json(result),),
        )


def load_result(path: Path) -> dict[str, Any] | None:
    init_db(path)
    with connect(path) as connection:
        row = connection.execute("SELECT value FROM state WHERE key='latest'").fetchone()
    return _loads(row[0]) if row else None


def seed_presets(path: Path, presets: list[Any]) -> None:
    init_db(path)
    now = _utc_now()
    with connect(path) as connection:
        for preset in presets:
            payload = preset.model_dump(mode="json") if hasattr(preset, "model_dump") else preset
            name = payload["name"]
            config_hash = getattr(preset, "config_hash", "")
            connection.execute(
                """
                INSERT INTO presets(id,name,built_in,config_hash,config_json,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(name) DO UPDATE SET
                    built_in=1,config_hash=excluded.config_hash,
                    config_json=excluded.config_json,updated_at=excluded.updated_at
                WHERE presets.built_in=1
                """,
                (f"builtin:{name}", name, 1, config_hash, _json(payload), now, now),
            )


def _preset_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"], "name": row["name"], "built_in": bool(row["built_in"]),
        "config_hash": row["config_hash"], "config": _loads(row["config_json"], {}),
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def list_presets(path: Path) -> list[dict[str, Any]]:
    init_db(path)
    with connect(path) as connection:
        rows = connection.execute(
            "SELECT * FROM presets ORDER BY built_in DESC, name COLLATE NOCASE"
        ).fetchall()
    return [_preset_row(row) for row in rows]


def get_preset(path: Path, preset_id: str) -> dict[str, Any] | None:
    init_db(path)
    with connect(path) as connection:
        row = connection.execute("SELECT * FROM presets WHERE id=?", (preset_id,)).fetchone()
    return _preset_row(row) if row else None


def create_preset(path: Path, name: str, config: dict[str, Any], config_hash: str) -> dict[str, Any]:
    init_db(path)
    preset_id, now = str(uuid4()), _utc_now()
    with connect(path) as connection:
        connection.execute(
            "INSERT INTO presets(id,name,built_in,config_hash,config_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (preset_id, name, 0, config_hash, _json(config), now, now),
        )
    result = get_preset(path, preset_id)
    assert result is not None
    return result


def update_preset(
    path: Path, preset_id: str, name: str, config: dict[str, Any], config_hash: str
) -> dict[str, Any] | None:
    init_db(path)
    with connect(path) as connection:
        existing = connection.execute("SELECT built_in FROM presets WHERE id=?", (preset_id,)).fetchone()
        if not existing:
            return None
        if existing["built_in"]:
            raise ValueError("built-in presets cannot be modified; duplicate it instead")
        connection.execute(
            "UPDATE presets SET name=?,config_hash=?,config_json=?,updated_at=? WHERE id=?",
            (name, config_hash, _json(config), _utc_now(), preset_id),
        )
    return get_preset(path, preset_id)


def delete_preset(path: Path, preset_id: str) -> bool:
    init_db(path)
    with connect(path) as connection:
        existing = connection.execute("SELECT built_in FROM presets WHERE id=?", (preset_id,)).fetchone()
        if not existing:
            return False
        if existing["built_in"]:
            raise ValueError("built-in presets cannot be deleted")
        connection.execute("DELETE FROM presets WHERE id=?", (preset_id,))
    return True


def create_run(
    path: Path,
    config: dict[str, Any],
    config_hash: str,
    provider: str,
    git_commit: str | None,
    app_version: str,
) -> str:
    init_db(path)
    run_id, launched = str(uuid4()), _utc_now()
    period = config["period"]
    instruments = config["instruments"]["pairs"] + [config["instruments"]["dxy_instrument"]]
    with connect(path) as connection:
        connection.execute(
            """
            INSERT INTO backtest_runs(
                id,name,status,progress_step,progress_pct,period_start,period_end,launched_at,
                preset_name,config_hash,config_json,provider,instruments_json,strategy_version,
                git_commit,app_version,execution_model
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id, config["name"], "QUEUED", "QUEUED", 0.0,
                str(period["start"]), str(period["end"]), launched,
                config.get("preset_name"), config_hash, _json(config), provider,
                _json(instruments), config["strategy_version"], git_commit, app_version,
                config["execution"]["price_mode"],
            ),
        )
    return run_id


def update_run_progress(
    path: Path,
    run_id: str,
    status: str,
    progress_pct: float,
    *,
    error_message: str | None = None,
    error_traceback: str | None = None,
) -> None:
    if status not in RUN_STATUSES:
        raise ValueError(f"unknown run status: {status}")
    init_db(path)
    now = _utc_now()
    with connect(path) as connection:
        if status == "LOADING_DATA":
            connection.execute(
                "UPDATE backtest_runs SET status=?,progress_step=?,progress_pct=?,"
                "started_at=COALESCE(started_at,?) WHERE id=?",
                (status, status, float(progress_pct), now, run_id),
            )
        elif status in {"COMPLETED", "FAILED"}:
            connection.execute(
                """
                UPDATE backtest_runs SET status=?,progress_step=?,progress_pct=?,finished_at=?,
                    duration_seconds=CASE WHEN started_at IS NULL THEN NULL
                    ELSE (julianday(?) - julianday(started_at)) * 86400 END,
                    error_message=?,error_traceback=? WHERE id=?
                """,
                (status, status, float(progress_pct), now, now, error_message, error_traceback, run_id),
            )
        else:
            connection.execute(
                "UPDATE backtest_runs SET status=?,progress_step=?,progress_pct=? WHERE id=?",
                (status, status, float(progress_pct), run_id),
            )


def save_run_output(
    path: Path,
    run_id: str,
    result: dict[str, Any],
    trades: list[dict[str, Any]],
    rejected: list[dict[str, Any]],
    stats: list[dict[str, Any]],
    *,
    candle_count: int,
    data_warnings: list[dict[str, Any] | str],
) -> None:
    init_db(path)
    with connect(path) as connection:
        connection.execute("DELETE FROM trades WHERE run_id=?", (run_id,))
        connection.execute("DELETE FROM rejected_setups WHERE run_id=?", (run_id,))
        connection.execute("DELETE FROM run_stats WHERE run_id=?", (run_id,))
        for sequence, trade in enumerate(trades):
            trade_id = str(trade.get("trade_id") or uuid4())
            payload = dict(trade, trade_id=trade_id, backtest_run_id=run_id)
            connection.execute(
                """INSERT INTO trades(
                    run_id,trade_id,sequence,trade_date,pair,direction,session,previous_session,
                    entry_time,exit_time,outcome,r_multiple,return_pct,equity_before,equity_after,
                    zone_timeframe,trigger_timeframe,spread_pips,data_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, trade_id, sequence, payload.get("trade_date"), payload.get("pair"),
                    payload.get("direction"), payload.get("session"), payload.get("previous_session"),
                    payload.get("entry_time"), payload.get("exit_time"), payload.get("outcome"),
                    payload.get("r_multiple"), payload.get("return_pct"), payload.get("equity_before"),
                    payload.get("equity_after"), payload.get("zone_tf"),
                    payload.get("trigger_timeframe", "M1"), payload.get("spread_pips"), _json(payload),
                ),
            )
        for sequence, setup in enumerate(rejected):
            setup_id = str(setup.get("setup_id") or uuid4())
            payload = dict(setup, setup_id=setup_id, backtest_run_id=run_id)
            connection.execute(
                """INSERT INTO rejected_setups(
                    run_id,setup_id,sequence,trade_date,pair,direction,session,reason,trace_json,data_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    run_id, setup_id, sequence, payload.get("trade_date"), payload.get("pair"),
                    payload.get("direction"), payload.get("session"), payload["reason"],
                    _json(payload.get("trace", [])), _json(payload),
                ),
            )
        for item in stats:
            connection.execute(
                "INSERT INTO run_stats(run_id,scope,dimension,bucket,metrics_json) VALUES(?,?,?,?,?)",
                (run_id, item["scope"], item["dimension"], str(item["bucket"]), _json(item["metrics"])),
            )
        connection.execute(
            "UPDATE backtest_runs SET candle_count=?,data_warnings_json=?,result_json=? WHERE id=?",
            (int(candle_count), _json(data_warnings), _json(result), run_id),
        )


def _run_row(row: sqlite3.Row, include_payload: bool = False) -> dict[str, Any]:
    compact_result = _loads(row["result_json"], {}) or {}
    output = {
        "id": row["id"], "name": row["name"], "status": row["status"],
        "progress_step": row["progress_step"], "progress_pct": row["progress_pct"],
        "period_start": row["period_start"], "period_end": row["period_end"],
        "launched_at": row["launched_at"], "started_at": row["started_at"],
        "finished_at": row["finished_at"], "duration_seconds": row["duration_seconds"],
        "preset_name": row["preset_name"], "config_hash": row["config_hash"],
        "provider": row["provider"], "instruments": _loads(row["instruments_json"], []),
        "strategy_version": row["strategy_version"], "git_commit": row["git_commit"],
        "app_version": row["app_version"], "execution_model": row["execution_model"],
        "candle_count": row["candle_count"],
        "data_warnings": _loads(row["data_warnings_json"], []),
        "data_revision": compact_result.get("data_revision"),
        "error_message": row["error_message"],
        "summary": compact_result.get("summary"),
        "trade_count": compact_result.get("trade_count", compact_result.get("summary", {}).get("trades") if isinstance(compact_result.get("summary"), dict) else None),
        "rejected_count": compact_result.get("rejected_count"),
    }
    if include_payload:
        output["config"] = _loads(row["config_json"], {})
        output["result"] = _loads(row["result_json"])
        output["error_traceback"] = row["error_traceback"]
    return output


def list_runs(path: Path, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    init_db(path)
    with connect(path) as connection:
        rows = connection.execute(
            "SELECT * FROM backtest_runs ORDER BY launched_at DESC LIMIT ? OFFSET ?",
            (max(1, min(limit, 1000)), max(0, offset)),
        ).fetchall()
    return [_run_row(row) for row in rows]


def get_run(path: Path, run_id: str) -> dict[str, Any] | None:
    init_db(path)
    with connect(path) as connection:
        row = connection.execute("SELECT * FROM backtest_runs WHERE id=?", (run_id,)).fetchone()
    return _run_row(row, include_payload=True) if row else None


def _payload_rows(path: Path, query: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
    with connect(path) as connection:
        rows = connection.execute(query, params).fetchall()
    return [_loads(row["data_json"], {}) for row in rows]


def list_trades(path: Path, run_id: str) -> list[dict[str, Any]]:
    init_db(path)
    return _payload_rows(path, "SELECT data_json FROM trades WHERE run_id=? ORDER BY sequence", (run_id,))


def list_rejected(
    path: Path,
    run_id: str,
    *,
    pair: str | None = None,
    session: str | None = None,
    direction: str | None = None,
    reason: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    init_db(path)
    clauses, params = ["run_id=?"], [run_id]
    for column, value in (("pair", pair), ("session", session), ("direction", direction), ("reason", reason)):
        if value:
            clauses.append(f"{column}=?")
            params.append(value)
    if date_from:
        clauses.append("trade_date>=?")
        params.append(date_from)
    if date_to:
        clauses.append("trade_date<=?")
        params.append(date_to)
    query = "SELECT data_json FROM rejected_setups WHERE " + " AND ".join(clauses) + " ORDER BY sequence"
    return _payload_rows(path, query, tuple(params))


def get_stats(path: Path, run_id: str) -> list[dict[str, Any]]:
    init_db(path)
    with connect(path) as connection:
        rows = connection.execute(
            "SELECT scope,dimension,bucket,metrics_json FROM run_stats WHERE run_id=? "
            "ORDER BY scope,dimension,bucket", (run_id,),
        ).fetchall()
    return [
        {"scope": row["scope"], "dimension": row["dimension"], "bucket": row["bucket"],
         "metrics": _loads(row["metrics_json"], {})}
        for row in rows
    ]


def count_runs(path: Path) -> int:
    init_db(path)
    with connect(path) as connection:
        return int(connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0])
