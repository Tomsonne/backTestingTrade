from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
import resource
import sys
import time

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.backtest import run_backtest
from app.config import Settings
from app.research.golden import legacy_golden_differences, legacy_trade_digest


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Re-run the immutable local legacy reference and verify its trade digest"
    )
    parser.add_argument(
        "--fixture",
        type=Path,
        default=ROOT / "tests" / "fixtures" / "legacy_golden.json",
    )
    parser.add_argument(
        "--strict-data",
        action="store_true",
        help="abort on every unexpected market-open minute gap instead of reproducing the captured run",
    )
    args = parser.parse_args()
    expected = json.loads(args.fixture.read_text())
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    started = time.perf_counter()
    settings = Settings(
        backtest_start=expected["period"]["start"],
        backtest_end=expected["period"]["end"],
        data_validation_mode="strict" if args.strict_data else "permissive",
        auto_run_on_startup=False,
        enable_scheduler=False,
    )
    result = run_backtest(settings, now=pd.Timestamp(expected["period"]["end"]))
    elapsed = time.perf_counter() - started
    max_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS and KiB on Linux.
    max_rss_mb = max_rss / (1024 * 1024) if sys.platform == "darwin" else max_rss / 1024
    differences = legacy_golden_differences(result, expected)
    report = {
        "status": "PASS" if not differences else "FAIL",
        "candidate_count": result["candidate_count"],
        "trade_count": len(result["trades"]),
        "trade_digest": legacy_trade_digest(result["trades"]),
        "elapsed_seconds": round(elapsed, 3),
        "max_rss_mb": round(max_rss_mb, 1),
        "differences": differences,
    }
    print(json.dumps(report, indent=2))
    return int(bool(differences))


if __name__ == "__main__":
    raise SystemExit(main())
