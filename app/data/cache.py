from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from pathlib import Path
import tempfile
from typing import Any

import pandas as pd

from .base import empty_canonical_frame, normalize_canonical, utc_timestamp


class PartitionedParquetStore:
    """Monthly Parquet store with atomic writes and resumable day coverage."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def symbol_root(self, instrument: str) -> Path:
        return self.root / instrument / "M1"

    def partition_path(self, instrument: str, month: str) -> Path:
        return self.symbol_root(instrument) / f"{month}.parquet"

    def manifest_path(self, instrument: str) -> Path:
        return self.symbol_root(instrument) / "manifest.json"

    def metadata_path(self, instrument: str) -> Path:
        return self.symbol_root(instrument) / "instrument.json"

    def import_side_path(self, instrument: str, side: str, month: str) -> Path:
        return self.symbol_root(instrument) / ".import_sides" / side.upper() / f"{month}.parquet"

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(path)

    @staticmethod
    def _atomic_parquet(path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as handle:
            temporary = Path(handle.name)
        try:
            frame.to_parquet(temporary, compression="zstd")
            temporary.replace(path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def load_manifest(self, instrument: str) -> dict[str, Any]:
        path = self.manifest_path(instrument)
        if not path.exists():
            return {
                "provider": "dukascopy",
                "symbol": instrument,
                "timeframe": "1min",
                "timezone": "UTC",
                "completed_days": [],
                "partial_days": [],
                "partitions": {},
            }
        return json.loads(path.read_text(encoding="utf-8"))

    def save_metadata(self, instrument: str, metadata: dict[str, Any]) -> None:
        self._atomic_json(self.metadata_path(instrument), metadata)

    def load_metadata(self, instrument: str) -> dict[str, Any] | None:
        path = self.metadata_path(instrument)
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def completed_days(self, instrument: str) -> set[str]:
        return set(self.load_manifest(instrument).get("completed_days", []))

    def partial_days(self, instrument: str) -> set[str]:
        return set(self.load_manifest(instrument).get("partial_days", []))

    def write_day(
        self,
        instrument: str,
        day: date,
        frame: pd.DataFrame,
        provider_symbol: str,
        complete: bool,
        generated_at: pd.Timestamp,
    ) -> None:
        day_start = pd.Timestamp(day, tz="UTC")
        day_end = day_start + pd.Timedelta(days=1)
        month = day_start.strftime("%Y-%m")
        path = self.partition_path(instrument, month)
        existing = pd.read_parquet(path) if path.exists() else empty_canonical_frame()
        existing = normalize_canonical(existing)
        existing = existing.loc[(existing.index < day_start) | (existing.index >= day_end)]
        combined = normalize_canonical(pd.concat([existing, normalize_canonical(frame)]))
        if combined.empty:
            if path.exists():
                path.unlink()
        else:
            self._atomic_parquet(path, combined)

        manifest = self.load_manifest(instrument)
        completed = set(manifest.get("completed_days", []))
        partial = set(manifest.get("partial_days", []))
        day_key = day.isoformat()
        if complete:
            completed.add(day_key)
            partial.discard(day_key)
        else:
            partial.add(day_key)
            completed.discard(day_key)

        partitions = manifest.setdefault("partitions", {})
        if path.exists():
            digest = sha256(path.read_bytes()).hexdigest()
            partitions[month] = {
                "rows": len(combined),
                "start": combined.index.min().isoformat(),
                "end": combined.index.max().isoformat(),
                "sha256": digest,
            }
        else:
            partitions.pop(month, None)

        nonempty = list(partitions.values())
        manifest.update(
            {
                "provider": "dukascopy",
                "provider_symbol": provider_symbol,
                "symbol": instrument,
                "timeframe": "1min",
                "timezone": "UTC",
                "volume": "best bid and best ask volumes summed independently by Dukascopy",
                "generated_at": generated_at.isoformat(),
                "completed_days": sorted(completed),
                "partial_days": sorted(partial),
                "partitions": partitions,
                "rows": sum(int(item["rows"]) for item in nonempty),
                "start": min((item["start"] for item in nonempty), default=None),
                "end": max((item["end"] for item in nonempty), default=None),
            }
        )
        self._atomic_json(self.manifest_path(instrument), manifest)

    def read(self, instrument: str, start, end) -> pd.DataFrame:
        start_ts = utc_timestamp(start)
        end_ts = utc_timestamp(end)
        if start_ts >= end_ts:
            return empty_canonical_frame()
        first_month = start_ts.tz_localize(None).to_period("M")
        last_month = (end_ts - pd.Timedelta(nanoseconds=1)).tz_localize(None).to_period("M")
        parts = []
        for period in pd.period_range(first_month, last_month, freq="M"):
            path = self.partition_path(instrument, str(period))
            if path.exists():
                parts.append(pd.read_parquet(path))
        if not parts:
            return empty_canonical_frame()
        frame = normalize_canonical(pd.concat(parts))
        return frame.loc[(frame.index >= start_ts) & (frame.index < end_ts)].copy()

    def write_import_side(self, instrument: str, side: str, frame: pd.DataFrame) -> list[str]:
        side = side.upper()
        if side not in {"BID", "ASK"}:
            raise ValueError("side must be BID or ASK")
        if frame.empty:
            return []
        source = frame.copy()
        source.index = pd.to_datetime(source.index, utc=True)
        source.index.name = "timestamp"
        months = sorted(source.index.strftime("%Y-%m").unique())
        for month in months:
            path = self.import_side_path(instrument, side, month)
            existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
            part = source.loc[source.index.strftime("%Y-%m") == month]
            merged = pd.concat([existing, part]).sort_index(kind="mergesort")
            merged = merged.loc[~merged.index.duplicated(keep="last")]
            self._atomic_parquet(path, merged)
        return months

    def read_import_side(self, instrument: str, side: str, month: str) -> pd.DataFrame:
        path = self.import_side_path(instrument, side, month)
        if not path.exists():
            return pd.DataFrame()
        frame = pd.read_parquet(path)
        frame.index = pd.to_datetime(frame.index, utc=True)
        frame.index.name = "timestamp"
        return frame.sort_index(kind="mergesort").loc[lambda x: ~x.index.duplicated(keep="last")]

    def instruments(self) -> list[str]:
        if not self.root.exists():
            return []
        return sorted(path.parent.parent.name for path in self.root.glob("*/M1/manifest.json"))
