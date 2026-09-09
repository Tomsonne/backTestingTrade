from __future__ import annotations

from concurrent.futures import Executor
import csv
from io import StringIO
from itertools import product
import json
from pathlib import Path
import sqlite3
from statistics import fmean, pstdev
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field

from .config import Settings
from .data.dukascopy import DukascopyProvider
from .jobs import APP_VERSION, enqueue_research_run, execute
from .research.analysis import configuration_diff, research_risk, trade_metrics
from .research.config import StrategyConfig
from .research.presets import ensure_legacy_compatible
from .research.windows import walk_forward_windows
from .storage import (
    count_runs,
    create_preset,
    delete_preset,
    get_run,
    get_stats,
    list_presets,
    list_rejected,
    list_runs,
    list_trades,
<<<<<<< HEAD
    list_missing_data_events,
=======
>>>>>>> 853804b008cb85b1a2c913966f2c28e9a257535a
    load_result,
    update_preset,
)


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WalkForwardRequest(ApiModel):
    config: StrategyConfig
    training_months: int = Field(gt=0, le=120)
    testing_months: int = Field(gt=0, le=60)
    roll_months: int = Field(gt=0, le=60)
    execute: bool = False


class SensitivityRequest(ApiModel):
    config: StrategyConfig
    parameters: dict[str, list[Any]]
    execute: bool = False


class CompareRequest(ApiModel):
    run_ids: list[str] = Field(min_length=2, max_length=20)


class RunIdsRequest(ApiModel):
    run_ids: list[str] = Field(min_length=1, max_length=100)


class SensitivityResultsRequest(RunIdsRequest):
    parameter_paths: list[str] = Field(min_length=1, max_length=2)


class ResearchRiskRequest(ApiModel):
    run_ids: list[str] = Field(default_factory=list, max_length=100)
    free_parameters: int = Field(default=0, ge=0, le=1000)
    stability: float | None = Field(default=None, ge=0, le=1)


def _not_found(kind: str, identifier: str) -> HTTPException:
    return HTTPException(404, f"{kind} not found: {identifier}")


def _csv_response(rows: list[dict[str, Any]], filename: str) -> Response:
    flattened = []
    for row in rows:
        flattened.append({
            key: json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (dict, list, tuple)) else value
            for key, value in row.items()
        })
    fields = sorted(set().union(*(row.keys() for row in flattened))) if flattened else []
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    if fields:
        writer.writeheader()
        writer.writerows(flattened)
    return Response(
        buffer.getvalue(), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _json_response(payload: Any, filename: str) -> Response:
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _set_path(payload: dict[str, Any], path: str, value: Any) -> None:
    target: Any = payload
    parts = path.split(".")
    for key in parts[:-1]:
        if not isinstance(target, dict) or key not in target:
            raise ValueError(f"unknown parameter path: {path}")
        target = target[key]
    if not isinstance(target, dict) or parts[-1] not in target:
        raise ValueError(f"unknown parameter path: {path}")
    target[parts[-1]] = value


def _get_path(payload: dict[str, Any], path: str) -> Any:
    target: Any = payload
    for key in path.split("."):
        if not isinstance(target, dict) or key not in target:
            raise ValueError(f"unknown parameter path: {path}")
        target = target[key]
    return target


def build_api_router(settings: Settings, executor: Executor) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/config")
    def legacy_config():
        return {
            "provider": settings.data_provider,
            "dxy_source": settings.dxy_source,
            "execution_price_mode": settings.execution_price_mode,
            "timezone": settings.timezone,
            "backtest_start": settings.backtest_start,
            "app_version": APP_VERSION,
            "mode": "RESEARCH",
            "sessions": [
                {"name": item.name, "start": item.start.strftime("%H:%M"), "end": item.end.strftime("%H:%M")}
                for item in settings.sessions
            ],
            "variants": {"A.0": "A + H2", "A.1": "A + H4", "B.0": "B + H2", "B.1": "B + H4"},
            "scheduler": {"enabled": settings.enable_scheduler, "weekdays": "Mon-Fri", "minute_each_hour": settings.schedule_minute},
        }

    @router.get("/config/schema")
    def config_schema():
        presets = list_presets(settings.db_path)
        return {
            "schema": StrategyConfig.model_json_schema(),
            "default": presets[0]["config"] if presets else None,
            "quick_periods": ["1w", "1m", "3m", "6m", "ytd", "1y", "all"],
            "research_only": True,
        }

    @router.get("/result")
    def latest_result():
        result = load_result(settings.db_path)
        return result or {"status": "empty", "message": "No backtest has been saved yet."}

    @router.post("/run")
    def legacy_run():
        if settings.data_provider == "twelvedata" and not settings.twelve_data_api_key:
            raise HTTPException(400, "TWELVE_DATA_API_KEY is required for Twelve Data")
        executor.submit(execute, settings)
        return {"status": "started", "mode": "legacy"}

    @router.get("/presets")
    def presets():
        return list_presets(settings.db_path)

    @router.post("/presets", status_code=201)
    def save_preset(config: StrategyConfig):
        try:
            return create_preset(
                settings.db_path, config.name, config.model_dump(mode="json"), config.config_hash
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, f"A preset named {config.name!r} already exists") from exc

    @router.put("/presets/{preset_id}")
    def replace_preset(preset_id: str, config: StrategyConfig):
        try:
            result = update_preset(
                settings.db_path, preset_id, config.name, config.model_dump(mode="json"), config.config_hash
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, f"A preset named {config.name!r} already exists") from exc
        if result is None:
            raise _not_found("preset", preset_id)
        return result

    @router.delete("/presets/{preset_id}", status_code=204)
    def remove_preset(preset_id: str):
        try:
            removed = delete_preset(settings.db_path, preset_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if not removed:
            raise _not_found("preset", preset_id)
        return Response(status_code=204)

    @router.post("/backtests", status_code=202)
    def create_backtest(config: StrategyConfig):
        try:
            ensure_legacy_compatible(config)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        run_id = enqueue_research_run(settings, config, executor)
        return {"id": run_id, "status": "QUEUED"}

    @router.get("/backtests")
    def runs(limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0)):
        return list_runs(settings.db_path, limit, offset)

    @router.post("/backtests/compare")
    def compare_runs(request: CompareRequest):
        selected = []
        for run_id in request.run_ids:
            run = get_run(settings.db_path, run_id)
            if not run:
                raise _not_found("run", run_id)
            selected.append(run)
        comparisons = []
        for run in selected:
            monthly = ((run.get("result") or {}).get("analysis") or {}).get("monthly_r", [])
            comparisons.append(
                {
                    "id": run["id"], "name": run["name"], "status": run["status"],
                    "summary": (run.get("result") or {}).get("summary"),
                    "monthly_r": monthly,
                    "positive_months": sum(float(item.get("total_r", 0)) > 0 for item in monthly),
                    "negative_months": sum(float(item.get("total_r", 0)) < 0 for item in monthly),
                    "flat_months": sum(float(item.get("total_r", 0)) == 0 for item in monthly),
                }
            )
        return {
            "runs": comparisons,
            "parameter_differences": configuration_diff([run["config"] for run in selected]),
            "note": "The comparison reports robustness dimensions and does not rank a single historical winner.",
        }

    @router.get("/backtests/{run_id}")
    def run_detail(run_id: str):
        run = get_run(settings.db_path, run_id)
        if not run:
            raise _not_found("run", run_id)
        return run

    @router.get("/backtests/{run_id}/status")
    def run_status(run_id: str):
        run = get_run(settings.db_path, run_id)
        if not run:
            raise _not_found("run", run_id)
        return {key: run[key] for key in (
            "id", "status", "progress_step", "progress_pct", "launched_at",
            "started_at", "finished_at", "duration_seconds", "error_message",
        )}

    @router.get("/backtests/{run_id}/trades")
<<<<<<< HEAD
    def run_trades(run_id: str, quality: str = "ALL"):
        if not get_run(settings.db_path, run_id):
            raise _not_found("run", run_id)
        rows = list_trades(settings.db_path, run_id)
        if quality == "ALL":
            return rows
        if quality == "CLEAN":
            return [row for row in rows if row.get("data_quality_status") == "COMPLETE"]
        if quality in {"COMPLETE", "DEGRADED", "GAP_RESOLVED", "INDETERMINATE"}:
            return [row for row in rows if row.get("data_quality_status") == quality]
        raise HTTPException(422, "Unknown quality filter")

    @router.get("/backtests/{run_id}/data-quality")
    def run_data_quality(run_id: str):
        run = get_run(settings.db_path, run_id)
        if not run:
            raise _not_found("run", run_id)
        result = run.get("result") or {}
        return {"report": result.get("data_quality_report"),
                "comparison": result.get("analysis", {}).get("quality_comparison"),
                "events": list_missing_data_events(settings.db_path, run_id)}
=======
    def run_trades(run_id: str):
        if not get_run(settings.db_path, run_id):
            raise _not_found("run", run_id)
        return list_trades(settings.db_path, run_id)
>>>>>>> 853804b008cb85b1a2c913966f2c28e9a257535a

    @router.get("/backtests/{run_id}/rejected")
    def run_rejected(
        run_id: str,
        pair: str | None = None,
        session: str | None = None,
        direction: str | None = None,
        reason: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ):
        if not get_run(settings.db_path, run_id):
            raise _not_found("run", run_id)
        return list_rejected(
            settings.db_path, run_id, pair=pair, session=session, direction=direction, reason=reason,
            date_from=date_from, date_to=date_to,
        )

    @router.get("/backtests/{run_id}/stats")
    def run_statistics(run_id: str):
        if not get_run(settings.db_path, run_id):
            raise _not_found("run", run_id)
        return get_stats(settings.db_path, run_id)

    @router.get("/backtests/{run_id}/export/{kind}")
    def export_run(run_id: str, kind: str):
        run = get_run(settings.db_path, run_id)
        if not run:
            raise _not_found("run", run_id)
        if kind == "trades.csv":
            return _csv_response(list_trades(settings.db_path, run_id), f"{run_id}-trades.csv")
        if kind == "rejected.csv":
            return _csv_response(list_rejected(settings.db_path, run_id), f"{run_id}-rejected.csv")
        if kind == "stats.csv":
            return _csv_response(get_stats(settings.db_path, run_id), f"{run_id}-stats.csv")
<<<<<<< HEAD
        if kind == "gaps.csv":
            return _csv_response(list_missing_data_events(settings.db_path, run_id), f"{run_id}-gaps.csv")
        if kind == "quality.json":
            return _json_response(run_data_quality(run_id), f"{run_id}-quality.json")
        if kind == "config.json":
            return _json_response(run["config"], f"{run_id}-config.json")
        if kind == "run.json":
            return _json_response({**run, "trades": list_trades(settings.db_path, run_id),
                                   "rejected_setups": list_rejected(settings.db_path, run_id),
                                   "missing_data_events": list_missing_data_events(settings.db_path, run_id)}, f"{run_id}.json")
        raise HTTPException(400, "Unknown export. Use trades.csv, rejected.csv, stats.csv, gaps.csv, quality.json, config.json or run.json")
=======
        if kind == "config.json":
            return _json_response(run["config"], f"{run_id}-config.json")
        if kind == "run.json":
            return _json_response(run, f"{run_id}.json")
        raise HTTPException(400, "Unknown export. Use trades.csv, rejected.csv, stats.csv, config.json or run.json")
>>>>>>> 853804b008cb85b1a2c913966f2c28e9a257535a

    @router.get("/data/status")
    def data_status():
        provider = DukascopyProvider(settings.data_dir, validation_mode="permissive")
        return {"provider": "dukascopy", "instruments": provider.status()}

    @router.post("/research/walk-forward")
    def walk_forward(request: WalkForwardRequest):
        windows = walk_forward_windows(
            request.config.period.start, request.config.period.end,
            request.training_months, request.testing_months, request.roll_months,
        )
        run_ids = []
        if request.execute:
            for window in windows:
                payload = request.config.model_dump(mode="json")
                payload["name"] = f"{request.config.name} — WF {window['window']}"
                payload["period"] = {
                    "start": window["training"]["start"], "end": window["testing"]["end"]
                }
                payload["oos"] = {
                    "enabled": True, "training": window["training"], "testing": window["testing"]
                }
                run_ids.append(enqueue_research_run(settings, StrategyConfig.model_validate(payload), executor))
        return {"windows": windows, "run_ids": run_ids, "automatic_optimization": False}

    @router.post("/research/walk-forward/aggregate")
    def aggregate_walk_forward(request: RunIdsRequest):
        selected, pending, oos_trades, per_window = [], [], [], []
        for run_id in request.run_ids:
            run = get_run(settings.db_path, run_id)
            if not run:
                raise _not_found("run", run_id)
            if run["status"] != "COMPLETED":
                pending.append({"id": run_id, "status": run["status"]})
                continue
            oos = (run.get("config") or {}).get("oos") or {}
            testing = oos.get("testing")
            if not oos.get("enabled") or not testing:
                raise HTTPException(422, f"Run {run_id} is not a walk-forward/OOS run")
            window_trades = [
                trade for trade in list_trades(settings.db_path, run_id)
                if testing["start"] <= str(trade.get("trade_date", "")) <= testing["end"]
            ]
            selected.append(run)
            oos_trades.extend(window_trades)
            per_window.append(
                {
                    "id": run_id,
                    "name": run["name"],
                    "testing": testing,
                    "metrics": trade_metrics(
                        window_trades,
                        float(run["config"]["risk"]["starting_equity"]),
                    ),
                }
            )
        starting_equity = (
            float(selected[0]["config"]["risk"]["starting_equity"]) if selected else 10000.0
        )
        return {
            "completed_windows": len(selected),
            "pending": pending,
            "oos_metrics": trade_metrics(oos_trades, starting_equity),
            "windows": per_window,
            "note": (
                "OOS trades are concatenated in time. Overlapping testing windows can count the same "
                "market period more than once and must be interpreted explicitly."
            ),
        }

    @router.post("/research/sensitivity")
    def sensitivity(request: SensitivityRequest):
        if not request.parameters:
            raise HTTPException(422, "At least one parameter range is required")
        names = list(request.parameters)
        combinations = list(product(*(request.parameters[name] for name in names)))
        if len(combinations) > 25:
            raise HTTPException(422, "Sensitivity runs are limited to 25 explicit combinations")
        variants, run_ids = [], []
        for number, values in enumerate(combinations, 1):
            payload = request.config.model_dump(mode="json")
            for path, value in zip(names, values):
                try:
                    _set_path(payload, path, value)
                except ValueError as exc:
                    raise HTTPException(422, str(exc)) from exc
            payload["name"] = f"{request.config.name} — sensitivity {number}"
            payload["preset_name"] = None
            payload["strategy_version"] = "riseup-v2"
            config = StrategyConfig.model_validate(payload)
            variants.append({"config_hash": config.config_hash, "values": dict(zip(names, values))})
            if request.execute:
                run_ids.append(enqueue_research_run(settings, config, executor))
        return {
            "variants": variants, "run_ids": run_ids,
            "warning": "Inspect neighboring values and OOS stability; this is not a best-strategy finder.",
        }

    @router.post("/research/sensitivity/results")
    def sensitivity_results(request: SensitivityResultsRequest):
        rows, pending = [], []
        for run_id in request.run_ids:
            run = get_run(settings.db_path, run_id)
            if not run:
                raise _not_found("run", run_id)
            if run["status"] != "COMPLETED":
                pending.append({"id": run_id, "status": run["status"]})
                continue
            try:
                values = {
                    path: _get_path(run["config"], path) for path in request.parameter_paths
                }
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            rows.append(
                {
                    "id": run_id,
                    "name": run["name"],
                    "values": values,
                    "metrics": (run.get("result") or {}).get("summary") or {},
                }
            )
        totals = [float(row["metrics"].get("total_r", 0) or 0) for row in rows]
        mean_absolute = fmean(abs(value) for value in totals) if totals else 0.0
        stability = (
            max(0.0, min(1.0, 1 - pstdev(totals) / mean_absolute))
            if len(totals) > 1 and mean_absolute
            else (1.0 if len(totals) == 1 else None)
        )
        isolated_peak = False
        if len(rows) >= 3 and len(request.parameter_paths) == 1:
            path = request.parameter_paths[0]
            ordered = sorted(rows, key=lambda row: row["values"][path])
            best_index = max(
                range(len(ordered)), key=lambda index: float(ordered[index]["metrics"].get("total_r", 0) or 0)
            )
            best = float(ordered[best_index]["metrics"].get("total_r", 0) or 0)
            neighbors = [
                float(ordered[index]["metrics"].get("total_r", 0) or 0)
                for index in (best_index - 1, best_index + 1)
                if 0 <= index < len(ordered)
            ]
            isolated_peak = bool(best > 0 and neighbors and all(value < best * 0.5 for value in neighbors))
        return {
            "rows": rows,
            "pending": pending,
            "stability": stability,
            "isolated_peak": isolated_peak,
            "warning": (
                "An isolated historical peak is visible; neighboring settings degrade sharply."
                if isolated_peak
                else "Interpret the grid with OOS evidence; it does not select a winner."
            ),
        }

    @router.post("/research/risk")
    def overfitting_risk(request: ResearchRiskRequest):
        selected = [get_run(settings.db_path, run_id) for run_id in request.run_ids]
        selected = [run for run in selected if run]
        is_metrics = oos_metrics = None
        if selected:
            scopes = ((selected[-1].get("result") or {}).get("analysis") or {}).get("scope_metrics", {})
            is_metrics, oos_metrics = scopes.get("IS"), scopes.get("OOS")
        return research_risk(
            count_runs(settings.db_path), request.free_parameters, len(selected),
            is_metrics, oos_metrics, request.stability,
        )

    return router
