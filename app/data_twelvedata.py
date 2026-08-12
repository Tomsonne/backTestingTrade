from __future__ import annotations
from datetime import datetime, timedelta, timezone
from pathlib import Path
import threading
import time
import httpx
import pandas as pd

from .config import Settings

def _utc_timestamp(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")

SYMBOL_MAP = {
    "EUR_USD": "EUR/USD",
    "GBP_USD": "GBP/USD",
    "USD_JPY": "USD/JPY",
    "USD_CAD": "USD/CAD",
    "USD_SEK": "USD/SEK",
    "USD_CHF": "USD/CHF",
}

class TwelveDataError(RuntimeError):
    pass

class CreditLimiter:
    """Simple limiteur local adapté au Basic plan (8 crédits/minute par défaut)."""
    def __init__(self, credits_per_minute: int):
        self.limit = max(1, credits_per_minute)
        self.lock = threading.Lock()
        self.window_started = 0.0
        self.used = 0

    def acquire(self, credits: int = 1) -> None:
        with self.lock:
            now = time.monotonic()
            if self.window_started == 0.0 or now - self.window_started >= 60:
                self.window_started = now
                self.used = 0
            if self.used + credits > self.limit:
                wait = max(0.0, 60 - (now - self.window_started)) + 0.25
                time.sleep(wait)
                self.window_started = time.monotonic()
                self.used = 0
            self.used += credits

class TwelveDataClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        if not settings.twelve_data_api_key:
            raise TwelveDataError(
                "TWELVE_DATA_API_KEY manquante. Crée une clé gratuite Twelve Data puis ajoute-la au .env."
            )
        self.client = httpx.Client(base_url=settings.twelve_data_base_url, timeout=60.0)
        self.limiter = CreditLimiter(settings.twelve_data_credits_per_minute)

    @staticmethod
    def _fmt(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    def fetch_time_series(
        self,
        internal_symbol: str,
        start: datetime,
        end: datetime,
        interval: str = "1min",
    ) -> pd.DataFrame:
        symbol = SYMBOL_MAP.get(internal_symbol, internal_symbol)
        self.limiter.acquire(1)
        params = {
            "symbol": symbol,
            "interval": interval,
            "start_date": self._fmt(start),
            "end_date": self._fmt(end),
            "timezone": "UTC",
            "apikey": self.settings.twelve_data_api_key,
            "format": "JSON",
        }
        r = self.client.get("/time_series", params=params)
        if r.status_code != 200:
            raise TwelveDataError(f"Twelve Data HTTP {r.status_code}: {r.text[:500]}")
        payload = r.json()
        if payload.get("status") == "error" or "values" not in payload:
            code = payload.get("code", "?")
            msg = payload.get("message", str(payload)[:500])
            raise TwelveDataError(f"Twelve Data erreur {code}: {msg}")

        rows = []
        for x in payload.get("values", []):
            # Forex /time_series fournit OHLC mais pas de volume historique.
            rows.append({
                "time": _utc_timestamp(x["datetime"]),
                "mid_o": float(x["open"]),
                "mid_h": float(x["high"]),
                "mid_l": float(x["low"]),
                "mid_c": float(x["close"]),
                "volume": float(x["volume"]) if x.get("volume") not in (None, "") else float("nan"),
            })
        if not rows:
            return pd.DataFrame(columns=["mid_o", "mid_h", "mid_l", "mid_c", "volume"])
        out = pd.DataFrame(rows).set_index("time").sort_index()
        return out[~out.index.duplicated(keep="last")]

class CandleCache:
    def __init__(self, settings: Settings, client: TwelveDataClient):
        self.settings = settings
        self.client = client
        self.root = settings.data_dir / "candles_twelvedata"
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, instrument: str, interval: str) -> Path:
        return self.root / f"{instrument}_{interval}.parquet"

    def _fetch_chunks(self, instrument: str, start: datetime, end: datetime, interval: str) -> pd.DataFrame:
        # 3 jours de M1 = 4320 points théoriques, sous le plafond officiel de 5000/réponse.
        chunk = timedelta(days=max(1, self.settings.twelve_data_chunk_days))
        parts = []
        cursor = start.astimezone(timezone.utc)
        end = end.astimezone(timezone.utc)
        while cursor < end:
            chunk_end = min(cursor + chunk, end)
            part = self.client.fetch_time_series(instrument, cursor, chunk_end, interval)
            if not part.empty:
                parts.append(part)
            cursor = chunk_end
        if not parts:
            return pd.DataFrame(columns=["mid_o", "mid_h", "mid_l", "mid_c", "volume"])
        out = pd.concat(parts).sort_index()
        return out[~out.index.duplicated(keep="last")]

    def get(self, instrument: str, start: datetime, end: datetime, interval: str = "1min") -> pd.DataFrame:
        path = self._path(instrument, interval)
        cached = pd.read_parquet(path) if path.exists() else pd.DataFrame()
        if not cached.empty:
            if cached.index.tz is None:
                cached.index = cached.index.tz_localize("UTC")
            else:
                cached.index = cached.index.tz_convert("UTC")

        parts = [cached] if not cached.empty else []

        if cached.empty or cached.index.min() > pd.Timestamp(start):
            older_end = cached.index.min().to_pydatetime() if not cached.empty else end
            older = self._fetch_chunks(instrument, start, older_end, interval)
            if not older.empty:
                parts.append(older)

        latest = cached.index.max().to_pydatetime() if not cached.empty else start
        if parts and cached.empty:
            latest = max(x.index.max().to_pydatetime() for x in parts if not x.empty)
        # Reprend 2 minutes pour éviter une coupure au bord du cache.
        latest = max(start, latest - timedelta(minutes=2))
        if latest < end:
            newer = self._fetch_chunks(instrument, latest, end, interval)
            if not newer.empty:
                parts.append(newer)

        if not parts:
            return pd.DataFrame(columns=["mid_o", "mid_h", "mid_l", "mid_c", "volume"])
        out = pd.concat(parts).sort_index()
        out = out[~out.index.duplicated(keep="last")]
        out.to_parquet(path)
        return out[(out.index >= pd.Timestamp(start)) & (out.index < pd.Timestamp(end))].copy()
