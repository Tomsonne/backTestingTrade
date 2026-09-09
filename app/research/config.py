from __future__ import annotations

from datetime import date, time
from hashlib import sha256
import json
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.indicators import PRICE_INDICATORS, VOLUME_INDICATORS


Pair = Literal["EUR_USD", "GBP_USD"]
Timeframe = Literal["H1", "H2", "H4"]
TriggerTimeframe = Literal["M1", "M5"]


class ResearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PeriodConfig(ResearchModel):
    start: date
    end: date

    @model_validator(mode="after")
    def validate_order(self) -> "PeriodConfig":
        if self.end < self.start:
            raise ValueError("period.end must be on or after period.start")
        return self


class InstrumentConfig(ResearchModel):
    pairs: list[Pair] = Field(default_factory=lambda: ["EUR_USD", "GBP_USD"])
    dxy_instrument: str = Field(default="DXY", min_length=1, max_length=80)
    dxy_source: Literal["dukascopy_direct", "synthetic"] = "dukascopy_direct"
    synthetic_fallback: bool = True

    @model_validator(mode="after")
    def require_pair(self) -> "InstrumentConfig":
        self.pairs = list(dict.fromkeys(self.pairs))
        if not self.pairs:
            raise ValueError("at least one pair must be enabled")
        return self


class SessionConfig(ResearchModel):
    name: str = Field(min_length=1, max_length=40)
    start: time
    end: time
    timezone: str | None = None
    enabled: bool = True
    order: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def validate_duration(self) -> "SessionConfig":
        if self.start == self.end:
            raise ValueError("session start and end must differ")
        if self.timezone:
            try:
                ZoneInfo(self.timezone)
            except Exception as exc:
                raise ValueError(f"invalid session timezone: {exc}") from exc
        return self


class DivergenceConfig(ResearchModel):
    enabled: bool = True
    previous_session: Literal["chronological", "named"] = "chronological"
    previous_session_name: str | None = None
    break_high: bool = True
    break_low: bool = True
    tolerance_pips: float = Field(default=0.0, ge=0, le=100)
    confirmation: Literal["intrabar", "interbar"] = "interbar"
    invalidate_if_dxy_confirms: bool = True
    max_delay_minutes: int | None = Field(default=None, ge=1, le=10080)
    dxy_gap_behavior: Literal["reject", "warn", "ignore_condition"] = "reject"

    @model_validator(mode="after")
    def validate_breaks(self) -> "DivergenceConfig":
        if self.enabled and not (self.break_high or self.break_low):
            raise ValueError("DXY divergence requires break_high and/or break_low")
        if self.previous_session == "named" and not self.previous_session_name:
            raise ValueError("previous_session_name is required in named mode")
        return self


class ZoneConfig(ResearchModel):
    timeframes: list[Timeframe] = Field(default_factory=lambda: ["H2"])
    match_mode: Literal["ANY", "ALL"] = "ANY"
    priority: list[Timeframe] = Field(default_factory=lambda: ["H1", "H2", "H4"])
    imbalance_min_adr_pct: float = Field(default=0.5, ge=0, le=100)
    adr_length: int = Field(default=15, ge=1, le=500)
    direction_match: bool = True
    max_distance_pips: float | None = Field(default=None, ge=0, le=10000)
    allow_touched: bool = True
    max_retests: int | None = Field(default=None, ge=0, le=100)
    expires_after_bars: int | None = Field(default=None, ge=1, le=100000)
    mitigation_mode: Literal["wick", "close"] = "wick"

    @model_validator(mode="after")
    def normalize_timeframes(self) -> "ZoneConfig":
        self.timeframes = list(dict.fromkeys(self.timeframes))
        self.priority = list(dict.fromkeys(self.priority))
        missing = [value for value in self.timeframes if value not in self.priority]
        self.priority.extend(missing)
        return self


class IndicatorSelection(ResearchModel):
    enabled: list[str] = Field(default_factory=lambda: list(PRICE_INDICATORS))
    required: list[str] = Field(default_factory=list)
    minimum_divergent: int = Field(default=1, ge=1, le=20)

    @model_validator(mode="after")
    def validate_names(self) -> "IndicatorSelection":
        known = set(PRICE_INDICATORS + VOLUME_INDICATORS)
        self.enabled = list(dict.fromkeys(self.enabled))
        self.required = list(dict.fromkeys(self.required))
        unknown = sorted((set(self.enabled) | set(self.required)) - known)
        if unknown:
            raise ValueError(f"unknown indicator(s): {', '.join(unknown)}")
        if not set(self.required).issubset(self.enabled):
            raise ValueError("required indicators must also be enabled")
        if self.minimum_divergent > len(self.enabled):
            raise ValueError("minimum_divergent cannot exceed enabled indicators")
        return self


class TriggerConfig(ResearchModel):
    mode: Literal["NONE", "FAST", "CONFIRMED", "CUSTOM"] = "CONFIRMED"
    timeframes: list[TriggerTimeframe] = Field(default_factory=lambda: ["M1"])
    left_bars: int = Field(default=5, ge=1, le=100)
    right_bars: int = Field(default=5, ge=1, le=100)
    check_cut_through: bool = True
    max_delay_minutes: int | None = Field(default=None, ge=1, le=10080)
    confirmation_type: Literal["next_open", "signal_close"] = "next_open"
    indicators: IndicatorSelection = Field(default_factory=IndicatorSelection)

    @model_validator(mode="after")
    def validate_timeframes(self) -> "TriggerConfig":
        self.timeframes = list(dict.fromkeys(self.timeframes))
        if self.mode != "NONE" and not self.timeframes:
            raise ValueError("a trigger timeframe is required unless mode is NONE")
        return self


class PairExitConfig(ResearchModel):
    stop_loss_pips: float = Field(gt=0, le=10000)
    take_profit_pips: float = Field(gt=0, le=10000)

    @property
    def reward_risk(self) -> float:
        return self.take_profit_pips / self.stop_loss_pips


class ExitConfig(ResearchModel):
    EUR_USD: PairExitConfig = Field(
        default_factory=lambda: PairExitConfig(stop_loss_pips=15, take_profit_pips=30)
    )
    GBP_USD: PairExitConfig = Field(
        default_factory=lambda: PairExitConfig(stop_loss_pips=20, take_profit_pips=40)
    )
    max_hold_minutes: int = Field(default=1440, ge=1, le=100000)


class RiskConfig(ResearchModel):
    starting_equity: float = Field(default=10000, gt=0)
    first_trade_risk_pct: float = Field(default=2, ge=0, le=20)
    second_trade_risk_pct: float = Field(default=1, ge=0, le=20)
    max_trades_per_day: int = Field(default=2, ge=1, le=100)
    stop_after_win: bool = True
    second_trade_after_loss: bool = True
    one_position_at_a_time: bool = True
    pair_priority: list[Pair] = Field(default_factory=lambda: ["EUR_USD", "GBP_USD"])
    max_daily_risk_pct: float = Field(default=3, ge=0, le=100)

    @model_validator(mode="after")
    def validate_daily_risk(self) -> "RiskConfig":
        self.pair_priority = list(dict.fromkeys(self.pair_priority))
        if self.first_trade_risk_pct > self.max_daily_risk_pct:
            raise ValueError("first trade risk exceeds max daily risk")
        if self.second_trade_risk_pct > self.max_daily_risk_pct:
            raise ValueError("second trade risk exceeds max daily risk")
        return self


class ExecutionConfig(ResearchModel):
    price_mode: Literal["mid", "bid_ask"] = "bid_ask"
    additional_spread_pips: float = Field(default=0, ge=0, le=100)
    slippage_pips: float = Field(default=0, ge=0, le=100)
    same_bar_policy: Literal["stop_first", "tp_first"] = "stop_first"


class DataValidationConfig(ResearchModel):
    mode: Literal["strict", "trace", "permissive"] = "strict"
    permissive_gap_limit_minutes: int = Field(default=5, ge=0, le=10080)


class OutOfSampleConfig(ResearchModel):
    enabled: bool = False
    training: PeriodConfig | None = None
    testing: PeriodConfig | None = None

    @model_validator(mode="after")
    def validate_split(self) -> "OutOfSampleConfig":
        if self.enabled:
            if not self.training or not self.testing:
                raise ValueError("enabled OOS validation requires training and testing periods")
            if self.training.end >= self.testing.start:
                raise ValueError("training period must end before testing starts")
        return self


class StrategyConfig(ResearchModel):
    schema_version: int = Field(default=2, ge=2)
    strategy_version: str = "riseup-v2"
    name: str = Field(default="Untitled research run", min_length=1, max_length=120)
    preset_name: str | None = None
    mode: Literal["RESEARCH"] = "RESEARCH"
    timezone: str = "Europe/Paris"
    htf_anchor_timezone: str = "Europe/Paris"
    volume_mode: Literal["legacy_no_volume", "dukascopy_volume"] = "legacy_no_volume"
    period: PeriodConfig
    instruments: InstrumentConfig = Field(default_factory=InstrumentConfig)
    sessions: list[SessionConfig]
    divergence: DivergenceConfig = Field(default_factory=DivergenceConfig)
    zones: ZoneConfig = Field(default_factory=ZoneConfig)
    trigger: TriggerConfig = Field(default_factory=TriggerConfig)
    exits: ExitConfig = Field(default_factory=ExitConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    validation: DataValidationConfig = Field(default_factory=DataValidationConfig)
    oos: OutOfSampleConfig = Field(default_factory=OutOfSampleConfig)

    @model_validator(mode="after")
    def validate_strategy(self) -> "StrategyConfig":
        try:
            ZoneInfo(self.timezone)
            ZoneInfo(self.htf_anchor_timezone)
        except Exception as exc:
            raise ValueError(f"invalid timezone: {exc}") from exc
        active = sorted((item for item in self.sessions if item.enabled), key=lambda item: item.order)
        if len(active) < 2:
            raise ValueError("at least two active sessions are required")
        names = [item.name for item in active]
        if len(names) != len(set(names)):
            raise ValueError("active session names must be unique")
        if self.divergence.previous_session_name and self.divergence.previous_session_name not in names:
            raise ValueError("previous_session_name must reference an active session")
        missing_priorities = set(self.instruments.pairs) - set(self.risk.pair_priority)
        if missing_priorities:
            raise ValueError(f"pair_priority is missing {sorted(missing_priorities)}")
        if self.volume_mode == "legacy_no_volume" and (
            set(self.trigger.indicators.enabled) & set(VOLUME_INDICATORS)
        ):
            raise ValueError("volume indicators require volume_mode=dukascopy_volume")
        if self.oos.enabled and self.oos.training and self.oos.testing:
            if self.oos.training.start < self.period.start or self.oos.testing.end > self.period.end:
                raise ValueError("IS/OOS periods must be contained in the backtest period")
        return self

    @property
    def config_hash(self) -> str:
        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return sha256(encoded.encode("utf-8")).hexdigest()

    @property
    def active_sessions(self) -> list[SessionConfig]:
        return sorted((item for item in self.sessions if item.enabled), key=lambda item: item.order)
