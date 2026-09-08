from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.research.config import StrategyConfig
from app.research.presets import builtin_presets, ensure_legacy_compatible


def test_legacy_presets_are_versioned_serializable_and_distinct():
    presets = builtin_presets()
    assert [preset.name for preset in presets] == [
        "Legacy A.0",
        "Legacy A.1",
        "Legacy B.0",
        "Legacy B.1",
    ]
    assert len({preset.config_hash for preset in presets}) == 4
    restored = StrategyConfig.model_validate_json(presets[0].model_dump_json())
    assert restored == presets[0]
    assert restored.strategy_version == "legacy-ab-v1"


def test_legacy_rules_are_immutable_but_period_and_validation_can_change():
    config = builtin_presets()[0]
    config.period.start = config.period.start.replace(day=2)
    config.validation.mode = "permissive"
    ensure_legacy_compatible(config)

    config.zones.timeframes = ["H4"]
    with pytest.raises(ValueError, match="immutable"):
        ensure_legacy_compatible(config)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("period", "end"), "2025-01-01"),
        (("instruments", "pairs"), []),
        (("exits", "EUR_USD", "stop_loss_pips"), 0),
        (("risk", "first_trade_risk_pct"), 101),
        (("zones", "timeframes"), ["H8"]),
        (("sessions",), []),
    ],
)
def test_strategy_config_rejects_unsafe_values(path, value):
    payload = builtin_presets()[0].model_dump(mode="json")
    target = payload
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    if path == ("period", "end"):
        payload["period"]["start"] = "2026-01-01"
    with pytest.raises(ValidationError):
        StrategyConfig.model_validate(deepcopy(payload))
