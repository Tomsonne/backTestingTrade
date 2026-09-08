from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.data.dukascopy import DukascopyProvider


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect the local Dukascopy M1 cache")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args()
    settings = Settings()
    provider = DukascopyProvider(settings.data_dir, validation_mode="permissive")
    rows = provider.status()
    if args.json:
        print(json.dumps({"provider": "dukascopy", "instruments": rows}, indent=2))
        return 0
    print("RiseUp historical database\n")
    if not rows:
        print("No Dukascopy M1 cache found.")
        return 0
    for item in rows:
        source = "Dukascopy direct" if item["symbol"] == "DXY" else "Dukascopy"
        print(item["symbol"])
        print(f"Source: {source} ({item['provider_symbol']})")
        print(f"From:   {item['start'] or '—'}")
        print(f"To:     {item['end'] or '—'}")
        print(f"M1 candles: {item['rows']:,}")
        print(f"Unexpected gaps: {item['unexpected_gaps']:,}")
        print(f"Completed/partial UTC days: {item['completed_days']}/{item['partial_days']}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
