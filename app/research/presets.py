from __future__ import annotations

from copy import deepcopy
from datetime import date

from .config import StrategyConfig


LEGACY_PRESET_NAMES = frozenset({"Legacy A.0", "Legacy A.1", "Legacy B.0", "Legacy B.1"})


def _legacy_payload(name: str, trigger_mode: str, timeframe: str) -> dict:
    return {
        "schema_version": 2,
        "strategy_version": "legacy-ab-v1",
        "name": name,
        "preset_name": name,
        "mode": "RESEARCH",
        "timezone": "Europe/Paris",
        "htf_anchor_timezone": "Europe/Paris",
        "volume_mode": "legacy_no_volume",
        "period": {"start": date(2026, 2, 1), "end": date(2026, 8, 25)},
        "instruments": {
            "pairs": ["EUR_USD", "GBP_USD"],
            "dxy_instrument": "DXY",
            "dxy_source": "dukascopy_direct",
            "synthetic_fallback": True,
        },
        "sessions": [
            {"name": "ASIA", "start": "23:00", "end": "06:00", "timezone": "Europe/Paris", "enabled": True, "order": 0},
            {"name": "BLUE", "start": "07:00", "end": "11:00", "timezone": "Europe/Paris", "enabled": True, "order": 1},
            {"name": "RED", "start": "12:00", "end": "16:00", "timezone": "Europe/Paris", "enabled": True, "order": 2},
        ],
        "divergence": {
            "enabled": True,
            "previous_session": "chronological",
            "break_high": True,
            "break_low": True,
            "tolerance_pips": 0,
            "confirmation": "interbar",
            "invalidate_if_dxy_confirms": True,
            "dxy_gap_behavior": "reject",
        },
        "zones": {
            "timeframes": [timeframe],
            "match_mode": "ANY",
            "priority": ["H1", "H2", "H4"],
            "imbalance_min_adr_pct": 0.5,
            "adr_length": 15,
            "direction_match": True,
            "allow_touched": True,
            "mitigation_mode": "wick",
        },
        "trigger": {
            "mode": trigger_mode,
            "timeframes": ["M1"],
            "left_bars": 5,
            "right_bars": 5,
            "check_cut_through": True,
            "confirmation_type": "next_open",
            "indicators": {
                "enabled": ["RSI", "MACD", "MACD_HIST", "MOMENTUM", "CCI", "STOCH", "DIOSC"],
                "required": [],
                "minimum_divergent": 1,
            },
        },
        "exits": {
            "EUR_USD": {"stop_loss_pips": 15, "take_profit_pips": 30},
            "GBP_USD": {"stop_loss_pips": 20, "take_profit_pips": 40},
            "max_hold_minutes": 1440,
        },
        "risk": {
            "starting_equity": 10000,
            "first_trade_risk_pct": 2,
            "second_trade_risk_pct": 1,
            "max_trades_per_day": 2,
            "stop_after_win": True,
            "second_trade_after_loss": True,
            "one_position_at_a_time": True,
            "pair_priority": ["EUR_USD", "GBP_USD"],
            "max_daily_risk_pct": 3,
        },
        "execution": {
            "price_mode": "bid_ask",
            "additional_spread_pips": 0,
            "slippage_pips": 0,
            "same_bar_policy": "stop_first",
        },
        "validation": {"mode": "strict", "permissive_gap_limit_minutes": 5},
        "oos": {"enabled": False},
    }


def builtin_presets() -> list[StrategyConfig]:
    definitions = (
        ("Legacy A.0", "FAST", "H2"),
        ("Legacy A.1", "FAST", "H4"),
        ("Legacy B.0", "CONFIRMED", "H2"),
        ("Legacy B.1", "CONFIRMED", "H4"),
    )
    return [StrategyConfig.model_validate(deepcopy(_legacy_payload(*definition))) for definition in definitions]


def ensure_legacy_compatible(config: StrategyConfig) -> None:
    """Prevent a run from claiming legacy semantics with altered trading rules.

    Run naming, period selection, data-validation policy and IS/OOS reporting do
    not change the strategy. Every other edit must be saved as an explicit V2
    variant so no unsupported legacy field is silently ignored.
    """

    tagged_legacy = config.preset_name in LEGACY_PRESET_NAMES
    versioned_legacy = config.strategy_version == "legacy-ab-v1"
    if not tagged_legacy and not versioned_legacy:
        return
    if not tagged_legacy or not versioned_legacy:
        raise ValueError(
            "Legacy preset name and strategy version must remain paired; duplicate the preset for V2 changes"
        )
    reference = next(item for item in builtin_presets() if item.name == config.preset_name)
    current_payload = config.model_dump(mode="json")
    reference_payload = reference.model_dump(mode="json")
    for mutable_metadata in ("name", "period", "validation", "oos"):
        current_payload.pop(mutable_metadata, None)
        reference_payload.pop(mutable_metadata, None)
    if current_payload != reference_payload:
        raise ValueError(
            "Built-in legacy trading rules are immutable; use 'Sauvegarder sous…' to create a V2 variant"
        )
