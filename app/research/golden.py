from __future__ import annotations

from hashlib import sha256
import json
from typing import Any


TRADE_DIGEST_FIELDS = (
    "variant",
    "pair",
    "direction",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "outcome",
    "r_multiple",
)


def legacy_trade_digest(trades: list[dict[str, Any]]) -> str:
    """Stable digest of the economically relevant legacy trade sequence."""

    payload = [{key: trade[key] for key in TRADE_DIGEST_FIELDS} for trade in trades]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def legacy_golden_differences(
    result: dict[str, Any], expected: dict[str, Any]
) -> list[str]:
    differences = []
    if result.get("candidate_count") != expected.get("candidate_count"):
        differences.append(
            f"candidate_count: {result.get('candidate_count')} != {expected.get('candidate_count')}"
        )
    if len(result.get("trades", [])) != expected.get("trade_count"):
        differences.append(
            f"trade_count: {len(result.get('trades', []))} != {expected.get('trade_count')}"
        )
    digest = legacy_trade_digest(result.get("trades", []))
    if digest != expected.get("trade_digest"):
        differences.append(f"trade_digest: {digest} != {expected.get('trade_digest')}")
    expected_by_variant = {item["variant"]: item for item in expected.get("summary", [])}
    for actual in result.get("summary", []):
        reference = expected_by_variant.get(actual.get("variant"))
        if reference is None:
            differences.append(f"unexpected summary variant: {actual.get('variant')}")
            continue
        for key in ("trades", "wins", "losses", "total_r"):
            if abs(float(actual.get(key, 0)) - float(reference.get(key, 0))) > 1e-9:
                differences.append(
                    f"{actual['variant']}.{key}: {actual.get(key)} != {reference.get(key)}"
                )
    return differences
