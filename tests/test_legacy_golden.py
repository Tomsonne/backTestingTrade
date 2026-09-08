from app.research.golden import legacy_golden_differences, legacy_trade_digest


def test_legacy_digest_is_stable_and_reports_economic_changes():
    trade = {
        "variant": "A.0",
        "pair": "EUR_USD",
        "direction": "long",
        "entry_time": "2026-01-02T07:00:00+00:00",
        "entry_price": 1.1,
        "exit_time": "2026-01-02T08:00:00+00:00",
        "exit_price": 1.2,
        "outcome": "WIN",
        "r_multiple": 2.0,
        "trace": ["ignored by economic digest"],
    }
    digest = legacy_trade_digest([trade])
    assert digest == legacy_trade_digest([{**trade, "trace": []}])
    expected = {
        "candidate_count": 1,
        "trade_count": 1,
        "trade_digest": digest,
        "summary": [{"variant": "A.0", "trades": 1, "wins": 1, "losses": 0, "total_r": 2}],
    }
    result = {
        "candidate_count": 1,
        "trades": [trade],
        "summary": [{"variant": "A.0", "trades": 1, "wins": 1, "losses": 0, "total_r": 2}],
    }
    assert legacy_golden_differences(result, expected) == []
    result["trades"][0]["entry_price"] = 1.1001
    assert any("trade_digest" in item for item in legacy_golden_differences(result, expected))
