from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.data.dukascopy import DUKASCOPY_SYMBOLS, DukascopyClient, DukascopyProvider
from app.data.factory import required_symbols


def _start(value: str) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    return timestamp.tz_localize("UTC") if timestamp.tzinfo is None else timestamp.tz_convert("UTC")


def _inclusive_end(value: str) -> pd.Timestamp:
    timestamp = _start(value)
    if len(value.strip()) == 10:
        timestamp += pd.Timedelta(days=1)
    return timestamp


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download and cache official Dukascopy M1 history.")
    parser.add_argument("--from", dest="start", required=True, help="UTC start date/time, inclusive")
    parser.add_argument("--to", dest="end", required=True, help="UTC end date/time; a date includes that full day")
    parser.add_argument("--symbols", nargs="+", choices=sorted(DUKASCOPY_SYMBOLS))
    parser.add_argument("--all-required", action="store_true", help="Use pairs plus configured DXY source")
    parser.add_argument("--interval", default="1min", choices=("1min", "M1"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--force", action="store_true", help="Refetch completed days, preserving existing observations")
    mode.add_argument("--repair-gaps", action="store_true", help="Fetch only days with missing open M1; insert only missing observations in the requested window")
    parser.add_argument("--confirm-provider-missing", action="store_true", help="During repair, confirm BOTH_MISSING with two successful raw BID/ASK fetches (completed UTC days only)")
    parser.add_argument("--recheck-provider-missing", action="store_true", help="Explicitly recheck stored confirmations; requires --confirm-provider-missing")
    parser.add_argument("--request-interval", type=float, default=1.0, help="Minimum seconds between HTTP requests (default: 1; one request in flight)")
    parser.add_argument("--validate", action="store_true", help="Run strict validation after download")
    args = parser.parse_args()
    if (args.confirm_provider_missing or args.recheck_provider_missing) and not args.repair_gaps:
        parser.error("Provider confirmation options require --repair-gaps")
    if args.recheck_provider_missing and not args.confirm_provider_missing:
        parser.error("--recheck-provider-missing requires --confirm-provider-missing")
    return args


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    symbols = args.symbols or required_symbols(settings)
    client = DukascopyClient(base_url=settings.dukascopy_base_url, request_interval_seconds=args.request_interval)
    provider = DukascopyProvider(
        settings.data_dir,
        validation_mode="strict" if args.validate else settings.data_validation_mode,
        anchor_timezone=settings.htf_anchor_timezone,
        client=client,
        workers=settings.dukascopy_workers,
    )
    start, end = _start(args.start), _inclusive_end(args.end)
    try:
        if args.repair_gaps:
            summary = provider.repair_gaps(symbols, start, end,
                                          confirm_provider_missing=args.confirm_provider_missing,
                                          recheck_provider_missing=args.recheck_provider_missing)
        else:
            summary = provider.download(symbols, start, end, force=args.force, validate=args.validate)
        if args.validate:
            for symbol in symbols:
                frame = provider.get(symbol, start, min(end, pd.Timestamp.now(tz="UTC").floor("min")), "1min")
                logging.info("[%s] strict validation OK - %d candle(s)", symbol, len(frame))
    finally:
        client.close()
    for symbol, stats in summary.items():
        logging.info("[%s] %s", symbol, stats)
    if args.repair_gaps:
        if any(stats["unconfirmed_missing_after"] for stats in summary.values()):
            return 1
        if any(stats["provider_confirmed_missing"] for stats in summary.values()):
            logging.warning("Repair finished with PROVIDER_CONFIRMED_MISSING: no automatic refetch; STRICT coverage remains incomplete")
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
