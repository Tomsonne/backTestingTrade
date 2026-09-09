from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.data.csv_import import DukascopyCsvImporter
from app.data.dukascopy import DUKASCOPY_SYMBOLS, DukascopyProvider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import official Dukascopy BID/ASK CSV exports.")
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--symbol", choices=sorted(DUKASCOPY_SYMBOLS))
    parser.add_argument("--side", choices=("BID", "ASK"))
    parser.add_argument("--timezone", help="Timezone for naive CSV timestamps; offset timestamps need none")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = Settings()
    importer = DukascopyCsvImporter(
        DukascopyProvider(settings.data_dir, validation_mode=settings.data_validation_mode)
    )
    for path in args.paths:
        result = importer.import_file(path, args.symbol, args.side, args.timezone)
        print(
            f"{path}: {result['instrument']} {result['side']} - "
            f"{result['source_rows']} source row(s), {result['materialized_rows']} canonical row(s)"
        )
        if result["pending_counterpart_months"]:
            print(
                "  Waiting for the matching BID/ASK export for: "
                + ", ".join(result["pending_counterpart_months"])
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
