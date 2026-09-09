from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.data.dukascopy import DukascopyProvider
from app.data.validation import validate_market_data


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List unexpected M1 gaps and split them by weekday vs weekend."
    )
    parser.add_argument("--symbol", choices=("EUR_USD", "GBP_USD", "DXY"), default="EUR_USD")
    parser.add_argument("--from", dest="start", required=True, help="UTC start date/time")
    parser.add_argument("--to", dest="end", required=True, help="UTC end date/time")
    parser.add_argument("--json", action="store_true", help="Emit the results as JSON")
    return parser.parse_args()


def _group_bucket(ts: pd.Timestamp) -> str:
    return "weekend" if ts.weekday() >= 5 else "weekday"


def _build_summary(report, start: pd.Timestamp, end: pd.Timestamp) -> dict:
    summary = {
        "weekday": {"minutes": 0, "events": 0, "days": {}},
        "weekend": {"minutes": 0, "events": 0, "days": {}},
        "total_minutes": 0,
        "total_events": 0,
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "gaps": [],
    }

    for gap in report.unexpected_gaps:
        gap_start = pd.Timestamp(gap.start)
        gap_end = pd.Timestamp(gap.end)
        bucket = _group_bucket(gap_start)
        day_key = gap_start.strftime("%Y-%m-%d")
        day_bucket = summary[bucket]["days"].setdefault(
            day_key,
            {
                "weekday": gap_start.strftime("%A"),
                "minutes": 0,
                "events": 0,
                "gaps": [],
            },
        )

        day_bucket["minutes"] += gap.missing_minutes
        day_bucket["events"] += 1
        day_bucket["gaps"].append(
            {
                "start": gap_start.isoformat(),
                "end": gap_end.isoformat(),
                "missing_minutes": gap.missing_minutes,
            }
        )

        summary[bucket]["minutes"] += gap.missing_minutes
        summary[bucket]["events"] += 1
        summary["total_minutes"] += gap.missing_minutes
        summary["total_events"] += 1
        summary["gaps"].append(
            {
                "start": gap_start.isoformat(),
                "end": gap_end.isoformat(),
                "missing_minutes": gap.missing_minutes,
                "bucket": bucket,
                "day": day_key,
                "weekday": gap_start.strftime("%A"),
            }
        )

    return summary


def _print_bucket(label: str, bucket: dict) -> None:
    print(f"=== {label} ===")
    print(f"Missing minutes: {bucket['minutes']}")
    print(f"Gap events: {bucket['events']}")
    if not bucket["days"]:
        print("No unexpected M1 gaps in this bucket.")
        print()
        return

    for day_key in sorted(bucket["days"]):
        day = bucket["days"][day_key]
        print(f"- {day_key} ({day['weekday']}): {day['minutes']} min in {day['events']} event(s)")
        for gap in day["gaps"]:
            print(
                f"    {gap['start']} -> {gap['end']}  |  {gap['missing_minutes']} minute(s)"
            )
    print()


def main() -> int:
    args = parse_args()
    settings = Settings()
    provider = DukascopyProvider(settings.data_dir, validation_mode="permissive")

    start = pd.Timestamp(args.start).tz_localize("UTC") if pd.Timestamp(args.start).tzinfo is None else pd.Timestamp(args.start).tz_convert("UTC")
    end = pd.Timestamp(args.end).tz_localize("UTC") if pd.Timestamp(args.end).tzinfo is None else pd.Timestamp(args.end).tz_convert("UTC")
    if end <= start:
        raise SystemExit("--to must be after --from")

    frame = provider.get(args.symbol, start, end, "1min")
    report = validate_market_data(frame, args.symbol, start, end)
    summary = _build_summary(report, start, end)

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"Gap report for {args.symbol}")
    print(f"Window: {start.isoformat()} -> {end.isoformat()}")
    print(f"Unexpected missing minutes total: {summary['total_minutes']}")
    print(f"Unexpected gap events total: {summary['total_events']}")
    print()

    _print_bucket("Weekday", summary["weekday"])
    _print_bucket("Weekend", summary["weekend"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
