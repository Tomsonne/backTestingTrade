from __future__ import annotations

from concurrent.futures import Executor
import logging
from pathlib import Path
import subprocess
from threading import Lock
import traceback

from .backtest import run_backtest
from .config import Settings
from .data.base import MarketDataProvider
from .research.config import StrategyConfig
from .research.engine import run_research_backtest
from .storage import (
    create_run,
    save_result,
    save_run_output,
    update_run_progress,
)


APP_VERSION = "2.1.0"
LOGGER = logging.getLogger(__name__)
_LEGACY_LOCK = Lock()


def git_commit(repository: Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repository or Path(__file__).resolve().parents[1],
            check=True, capture_output=True, text=True, timeout=3,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def execute(settings: Settings):
    """Historical synchronous entrypoint kept for CLI and scheduler compatibility."""
    if not _LEGACY_LOCK.acquire(blocking=False):
        return {"status": "already_running"}
    try:
        result = run_backtest(settings)
        save_result(settings.db_path, result)
        return {"status": "ok", "generated_at": result["generated_at"]}
    finally:
        _LEGACY_LOCK.release()


def enqueue_research_run(
    settings: Settings,
    config: StrategyConfig,
    executor: Executor,
    provider: MarketDataProvider | None = None,
) -> str:
    payload = config.model_dump(mode="json")
    run_id = create_run(
        settings.db_path, payload, config.config_hash, settings.data_provider,
        git_commit(), APP_VERSION,
    )
    executor.submit(execute_research_run, settings, run_id, config, provider)
    return run_id


def execute_research_run(
    settings: Settings,
    run_id: str,
    config: StrategyConfig,
    provider: MarketDataProvider | None = None,
) -> dict:
    def progress(step: str, percent: float) -> None:
        update_run_progress(settings.db_path, run_id, step, percent)
        LOGGER.info("[run=%s] %s %.0f%%", run_id, step, percent)

    try:
        output = run_research_backtest(config, settings, provider=provider, progress=progress)
        progress("SAVING_RESULTS", 95)
        save_run_output(
            settings.db_path, run_id, output.result, output.trades, output.rejected, output.stats,
            candle_count=output.candle_count, data_warnings=output.data_warnings,
        )
        save_result(settings.db_path, output.result)
        update_run_progress(settings.db_path, run_id, "COMPLETED", 100)
        LOGGER.info(
            "[run=%s] completed: %d trades, %d rejected setups",
            run_id, len(output.trades), len(output.rejected),
        )
        return {"status": "COMPLETED", "run_id": run_id}
    except Exception as exc:
        rendered = traceback.format_exc()
        update_run_progress(
            settings.db_path, run_id, "FAILED", 100,
            error_message=str(exc), error_traceback=rendered,
        )
        LOGGER.exception("[run=%s] failed: %s", run_id, exc)
        return {"status": "FAILED", "run_id": run_id, "error": str(exc)}
