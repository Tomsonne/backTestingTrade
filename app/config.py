from __future__ import annotations
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo
import json
import os

DEFAULT_SESSIONS = [
    {"name": "ASIA", "start": "23:00", "end": "06:00"},
    {"name": "BLUE", "start": "07:00", "end": "11:00"},
    {"name": "RED", "start": "12:00", "end": "16:00"},
]

@dataclass(frozen=True)
class SessionDef:
    name: str
    start: time
    end: time

    @property
    def overnight(self) -> bool:
        return self.end <= self.start


def _parse_time(value: str) -> time:
    hh, mm = map(int, value.split(":"))
    return time(hh, mm)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    # Twelve Data: aucune connexion à un compte de trading n'est requise.
    twelve_data_api_key: str = os.getenv("TWELVE_DATA_API_KEY", "")
    twelve_data_base_url: str = os.getenv("TWELVE_DATA_BASE_URL", "https://api.twelvedata.com")
    twelve_data_credits_per_minute: int = int(os.getenv("TWELVE_DATA_CREDITS_PER_MINUTE", "8"))
    twelve_data_chunk_days: int = int(os.getenv("TWELVE_DATA_CHUNK_DAYS", "3"))

    data_dir: Path = Path(os.getenv("DATA_DIR", "./data"))
    db_path: Path = Path(os.getenv("DB_PATH", "./data/riseup.sqlite3"))
    timezone: str = os.getenv("SESSION_TIMEZONE", "Europe/Paris")
    sessions_json: str = os.getenv("SESSIONS_JSON", json.dumps(DEFAULT_SESSIONS))
    backtest_start: str = os.getenv("BACKTEST_START", "2026-02-01")

    imbalance_min_adr_pct: float = float(os.getenv("IMBALANCE_MIN_ADR_PCT", "0.50"))
    adr_length: int = int(os.getenv("ADR_LENGTH", "15"))
    zone_direction_match: bool = _env_bool("ZONE_DIRECTION_MATCH", True)

    lb: int = int(os.getenv("M1_LEFT_BARS", "5"))
    rb: int = int(os.getenv("M1_RIGHT_BARS", "5"))
    showlimit: int = int(os.getenv("M1_MIN_DIVERGENCES", "1"))
    check_cut_through: bool = _env_bool("M1_CHECK_CUT_THROUGH", True)

    invalidate_if_dxy_confirms_before_entry: bool = _env_bool("INVALIDATE_IF_DXY_CONFIRMS", True)

    eurusd_sl_pips: float = float(os.getenv("EURUSD_SL_PIPS", "15"))
    eurusd_tp_pips: float = float(os.getenv("EURUSD_TP_PIPS", "30"))
    gbpusd_sl_pips: float = float(os.getenv("GBPUSD_SL_PIPS", "20"))
    gbpusd_tp_pips: float = float(os.getenv("GBPUSD_TP_PIPS", "40"))

    # Twelve Data ne fournit pas le bid/ask historique via time_series.
    # 0 = backtest sur OHLC mid/agrégé sans coût implicite. À calibrer ensuite.
    eurusd_spread_pips: float = float(os.getenv("EURUSD_SPREAD_PIPS", "0"))
    gbpusd_spread_pips: float = float(os.getenv("GBPUSD_SPREAD_PIPS", "0"))

    first_trade_risk_pct: float = float(os.getenv("FIRST_TRADE_RISK_PCT", "2"))
    second_trade_risk_pct: float = float(os.getenv("SECOND_TRADE_RISK_PCT", "1"))
    starting_equity: float = float(os.getenv("STARTING_EQUITY", "10000"))

    max_hold_minutes: int = int(os.getenv("MAX_HOLD_MINUTES", "1440"))
    same_bar_policy: str = os.getenv("SAME_BAR_POLICY", "stop_first")

    enable_scheduler: bool = _env_bool("ENABLE_SCHEDULER", True)
    auto_run_on_startup: bool = _env_bool("AUTO_RUN_ON_STARTUP", True)
    schedule_minute: int = int(os.getenv("SCHEDULE_MINUTE", "7"))
    pair_priority: tuple[str, ...] = ("EUR_USD", "GBP_USD")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def sessions(self) -> list[SessionDef]:
        raw = json.loads(self.sessions_json)
        return [SessionDef(x["name"], _parse_time(x["start"]), _parse_time(x["end"])) for x in raw]

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
