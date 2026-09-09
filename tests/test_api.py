from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.research.config import StrategyConfig
from app.storage import create_run, save_run_output, update_run_progress


def test_research_api_smoke_and_preset_lifecycle(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "lab.sqlite3",
        enable_scheduler=False,
        auto_run_on_startup=False,
    )
    with TestClient(create_app(settings)) as client:
        schema = client.get("/api/config/schema")
        assert schema.status_code == 200
        assert schema.json()["research_only"] is True

        presets = client.get("/api/presets").json()
        assert len(presets) == 4
        custom = presets[0]["config"]
        custom["name"] = "API custom"
        custom["preset_name"] = None
        custom["strategy_version"] = "riseup-v2"
        created = client.post("/api/presets", json=custom)
        assert created.status_code == 201
        preset_id = created.json()["id"]
        assert client.delete(f"/api/presets/{preset_id}").status_code == 204

        assert client.get("/api/backtests").json() == []
        altered_legacy = client.get("/api/presets").json()[0]["config"]
        altered_legacy["zones"]["timeframes"] = ["H4"]
        rejected_run = client.post("/api/backtests", json=altered_legacy)
        assert rejected_run.status_code == 422
        assert "immutable" in rejected_run.json()["detail"]
        quality = client.get("/api/data/status")
        assert quality.status_code == 200
        assert quality.json()["provider"] == "dukascopy"


def test_walk_forward_and_sensitivity_api_are_bounded(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "lab.sqlite3",
        enable_scheduler=False,
        auto_run_on_startup=False,
    )
    with TestClient(create_app(settings)) as client:
        config = client.get("/api/presets").json()[0]["config"]
        config["period"] = {"start": "2024-01-01", "end": "2026-12-31"}
        walk = client.post(
            "/api/research/walk-forward",
            json={"config": config, "training_months": 12, "testing_months": 3, "roll_months": 3},
        )
        assert walk.status_code == 200
        assert walk.json()["windows"]
        assert walk.json()["run_ids"] == []

        sensitivity = client.post(
            "/api/research/sensitivity",
            json={
                "config": config,
                "parameters": {
                    "exits.EUR_USD.stop_loss_pips": [12, 15, 18],
                    "exits.EUR_USD.take_profit_pips": [24, 30],
                },
            },
        )
        assert sensitivity.status_code == 200
        assert len(sensitivity.json()["variants"]) == 6


def test_walk_forward_aggregate_and_sensitivity_results(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        db_path=tmp_path / "lab.sqlite3",
        enable_scheduler=False,
        auto_run_on_startup=False,
    )
    with TestClient(create_app(settings)) as client:
        payload = client.get("/api/presets").json()[0]["config"]
        payload["name"] = "Window 1"
        payload["preset_name"] = None
        payload["strategy_version"] = "riseup-v2"
        payload["period"] = {"start": "2024-01-01", "end": "2024-06-30"}
        payload["oos"] = {
            "enabled": True,
            "training": {"start": "2024-01-01", "end": "2024-03-31"},
            "testing": {"start": "2024-04-01", "end": "2024-06-30"},
        }
        config = StrategyConfig.model_validate(payload)
        run_id = create_run(
            settings.db_path, config.model_dump(mode="json"), config.config_hash,
            "dukascopy", "test", "2.0.0",
        )
        trade = {
            "pair": "EUR_USD", "direction": "long", "trade_date": "2024-05-02",
            "session": "BLUE", "entry_time": "2024-05-02T07:00:00Z",
            "exit_time": "2024-05-02T08:00:00Z", "outcome": "WIN",
            "r_multiple": 2.0, "return_pct": 2.0, "equity_before": 10000,
            "equity_after": 10200,
        }
        save_run_output(
            settings.db_path, run_id,
            {"summary": {"trades": 1, "total_r": 2.0, "expectancy": 2.0}},
            [trade], [], [], candle_count=100, data_warnings=[],
        )
        update_run_progress(settings.db_path, run_id, "COMPLETED", 100)

        aggregate = client.post(
            "/api/research/walk-forward/aggregate", json={"run_ids": [run_id]}
        )
        assert aggregate.status_code == 200
        assert aggregate.json()["oos_metrics"]["total_r"] == 2.0

        sensitivity = client.post(
            "/api/research/sensitivity/results",
            json={
                "run_ids": [run_id],
                "parameter_paths": ["exits.EUR_USD.stop_loss_pips"],
            },
        )
        assert sensitivity.status_code == 200
        assert sensitivity.json()["rows"][0]["values"]["exits.EUR_USD.stop_loss_pips"] == 15
