from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.jobs import APP_VERSION, execute_research_run, git_commit
from app.research.config import StrategyConfig
from app.research.presets import builtin_presets
from app.storage import create_run, list_presets, seed_presets


LOGGER = logging.getLogger("riseup.research.cli")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a persistent RiseUp research backtest.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--preset", default="Legacy A.0", help="Saved preset name")
    source.add_argument("--config", type=Path, help="StrategyConfig JSON file")
    parser.add_argument("--from", dest="start", help="Override inclusive start date")
    parser.add_argument("--to", dest="end", help="Override inclusive end date")
    parser.add_argument("--name", help="Override run name")
    parser.add_argument("--validation", choices=("strict", "trace", "permissive"), help="Override data-quality policy without changing trading rules")
    parser.add_argument("--list-presets", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    seed_presets(settings.db_path, builtin_presets())
    saved = list_presets(settings.db_path)
    if args.list_presets:
        for item in saved:
            LOGGER.info("%s%s", item["name"], " [built-in]" if item["built_in"] else "")
        return 0
    if args.config:
        config = StrategyConfig.model_validate(json.loads(args.config.read_text(encoding="utf-8")))
    else:
        selected = next((item for item in saved if item["name"].casefold() == args.preset.casefold()), None)
        if not selected:
            LOGGER.error("Unknown preset %r. Use --list-presets.", args.preset)
            return 2
        config = StrategyConfig.model_validate(selected["config"])
    payload = config.model_dump(mode="json")
    if args.start:
        payload["period"]["start"] = args.start
    if args.end:
        payload["period"]["end"] = args.end
    if args.name:
        payload["name"] = args.name
    if args.validation:
        payload["validation"]["mode"] = args.validation
    config = StrategyConfig.model_validate(payload)
    run_id = create_run(
        settings.db_path, config.model_dump(mode="json"), config.config_hash,
        settings.data_provider, git_commit(ROOT), APP_VERSION,
    )
    LOGGER.info("[run=%s] queued", run_id)
    result = execute_research_run(settings, run_id, config)
    LOGGER.info("[run=%s] %s", run_id, result["status"])
    return 0 if result["status"] == "COMPLETED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
