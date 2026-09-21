from types import SimpleNamespace
import json

import httpx
import pandas as pd
import pytest
from pydantic import ValidationError

from app.data.base import DataCoverageError
from app.data.dukascopy import DukascopyClient, DukascopyError, DukascopyProvider, combine_bid_ask, decode_candle_payload
from app.data.gaps import GapCatalog, TraceProvider
from app.data.provider_missing import ProviderMissingConfirmation
from app.data.validation import validate_market_data
from scripts import download_history, gap_report


def payload(index, side):
    base = pd.Timestamp("2025-04-28T00:00Z")
    offsets = [int((t - base) / pd.Timedelta(minutes=1)) for t in index]
    return {"timestamp": int(base.timestamp() * 1000), "shift": 60000, "multiplier": 0.00001,
            "open": 1.1 + (0.0002 if side == "ASK" else 0),
            "high": 1.101 + (0.0002 if side == "ASK" else 0),
            "low": 1.099 + (0.0002 if side == "ASK" else 0),
            "close": 1.1 + (0.0002 if side == "ASK" else 0),
            "times": [t - (offsets[i - 1] if i else 0) for i, t in enumerate(offsets)],
            "opens": [0] * len(index), "highs": [0] * len(index), "lows": [0] * len(index),
            "closes": [0] * len(index), "volumes": [0.01] * len(index)}


@pytest.fixture
def rig(tmp_path):
    index = pd.date_range("2025-04-28T12:00Z", periods=3, freq="min")
    state = SimpleNamespace(index=index, sides={s: index.delete(1) for s in ("BID", "ASK")},
                            calls=[], fail_at=None, malformed=False, second_sides=None)

    def respond(request):
        side = request.url.path.split("/")[4]
        state.calls.append(side)
        if len(state.calls) == state.fail_at:
            return httpx.Response(503)
        indices = state.second_sides if state.second_sides and len(state.calls) > 2 else state.sides
        return httpx.Response(200, json={} if state.malformed else payload(indices[side], side))

    client = DukascopyClient(retries=1, request_interval_seconds=0)
    client.client.close()
    client.client = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(respond))
    provider = DukascopyProvider(tmp_path, client=client, validation_mode="permissive")
    provider.store.save_metadata("EUR_USD", {"holidays": []})
    original = combine_bid_ask(*(decode_candle_payload(payload(index.delete(1), side)) for side in ("BID", "ASK")))
    provider.store.write_day("EUR_USD", index[0].date(), original, "EUR-USD", True, pd.Timestamp.now(tz="UTC"))
    state.provider, state.original = provider, original
    state.start, state.end = index[0], index[-1] + pd.Timedelta(minutes=1)
    state.repair = lambda **kwargs: provider.repair_gaps(["EUR_USD"], state.start, state.end, **kwargs)["EUR_USD"]
    state.records = lambda: provider.store.load_confirmations("EUR_USD", state.start, state.end)
    yield state
    client.close()


@pytest.mark.parametrize("classification,bid,ask,confirmed,missing,calls", [
    ("BOTH_MISSING", False, False, 1, 1, 4),
    ("BID_ONLY", True, False, 0, 1, 2),
    ("ASK_ONLY", False, True, 0, 1, 2),
    ("BOTH_PRESENT", True, True, 0, 0, 2),
])
def test_raw_side_classification(rig, classification, bid, ask, confirmed, missing, calls):
    rig.sides = {"BID": rig.index if bid else rig.index.delete(1), "ASK": rig.index if ask else rig.index.delete(1)}
    result = rig.repair(confirm_provider_missing=True)
    assert result["provider_confirmed_missing"] == confirmed
    assert result["missing_after"] == missing
    assert result["unconfirmed_missing_after"] == missing - confirmed
    assert len(rig.calls) == calls
    assert len(rig.records()) == confirmed
    if confirmed:
        record = rig.records()[0]
        assert record.classification == "PROVIDER_CONFIRMED_MISSING"
        assert all(c.bid.http_status == c.ask.http_status == 200 for c in record.checks)
        assert all(not c.bid_present and not c.ask_present for c in record.checks)


def test_confirmation_requires_explicit_repair_and_never_reconstructs(rig):
    before = rig.provider.store.partition_path("EUR_USD", "2025-04").read_bytes()
    rig.repair()
    assert rig.records() == []
    rig.repair(confirm_provider_missing=True)
    assert rig.provider.store.partition_path("EUR_USD", "2025-04").read_bytes() == before
    pd.testing.assert_frame_equal(rig.provider.get("EUR_USD", rig.start, rig.end), rig.original)


@pytest.mark.parametrize("failure", [1, 3, 4])
def test_http_failure_never_confirms_even_after_first_success(rig, failure):
    rig.fail_at = failure
    with pytest.raises(DukascopyError):
        rig.repair(confirm_provider_missing=True)
    assert rig.records() == []


def test_malformed_http_200_is_not_evidence_of_absence(rig):
    rig.malformed = True
    with pytest.raises(DukascopyError, match="Malformed"):
        rig.repair(confirm_provider_missing=True)
    assert rig.records() == []


def test_decoder_loss_cannot_be_misclassified_as_provider_absence(rig, monkeypatch):
    rig.sides = {s: rig.index for s in ("BID", "ASK")}
    real_decode = decode_candle_payload
    monkeypatch.setattr("app.data.dukascopy.decode_candle_payload", lambda p: real_decode(p).iloc[:1])
    with pytest.raises(DukascopyError, match="mismatch"):
        rig.repair(confirm_provider_missing=True)
    assert rig.records() == []


def test_second_fetch_can_repair_instead_of_confirming(rig):
    rig.second_sides = {s: rig.index for s in ("BID", "ASK")}
    result = rig.repair(confirm_provider_missing=True)
    assert result["missing_after"] == 0
    assert result["added_rows"] == 1
    assert rig.records() == []


def test_cached_minute_is_never_confirmed_missing(rig):
    full = combine_bid_ask(*(decode_candle_payload(payload(rig.index, side)) for side in ("BID", "ASK")))
    rig.provider.store.write_day("EUR_USD", rig.index[0].date(), full, "EUR-USD", True, pd.Timestamp.now(tz="UTC"))
    result = rig.repair(confirm_provider_missing=True)
    assert result["missing_after"] == 0
    assert rig.calls == []
    assert rig.records() == []


def test_persistent_confirmation_skips_refetch_after_restart(rig):
    rig.repair(confirm_provider_missing=True)
    provider = DukascopyProvider(rig.provider.store.root.parent, client=rig.provider.client)
    for _ in range(3):
        result = provider.repair_gaps(["EUR_USD"], rig.start, rig.end)["EUR_USD"]
        assert result["downloaded_days"] == 0
        assert result["missing_after"] == result["provider_confirmed_missing"] == 1
    assert len(rig.calls) == 4


def test_explicit_recheck_accepts_later_real_bar_and_preserves_old_evidence(rig):
    rig.repair(confirm_provider_missing=True)
    rig.sides = {s: rig.index for s in ("BID", "ASK")}
    result = rig.repair(confirm_provider_missing=True, recheck_provider_missing=True)
    assert result["missing_after"] == result["provider_confirmed_missing"] == 0
    assert len(rig.calls) == 6
    assert len(rig.records()) == 1  # Historical proof retained, no longer active.
    rig.provider.validation_mode = "strict"
    assert len(rig.provider.get("EUR_USD", rig.start, rig.end)) == 3


def test_strict_blocks_confirmed_gap_without_recommending_impossible_repair(rig):
    rig.repair(confirm_provider_missing=True)
    rig.provider.validation_mode = "strict"
    with pytest.raises(DataCoverageError, match="PROVIDER_CONFIRMED_MISSING") as error:
        rig.provider.get("EUR_USD", rig.start, rig.end)
    assert "Run historical data repair first" not in str(error.value)
    assert "STRICT still requires complete observations" in str(error.value)


def test_trace_keeps_same_physical_gaps_and_never_exposes_future_boundary(rig):
    before = GapCatalog(rig.start, rig.end)
    frame_before = TraceProvider(rig.provider, before).get("EUR_USD", rig.start, rig.end)
    rig.repair(confirm_provider_missing=True)
    after = GapCatalog(rig.start, rig.end)
    frame_after = TraceProvider(rig.provider, after).get("EUR_USD", rig.start, rig.end)
    pd.testing.assert_frame_equal(frame_before, frame_after)
    assert before.physical_gaps() == after.physical_gaps()
    assert before.select("EUR_USD", rig.start, rig.index[2], "ENTRY") == after.select("EUR_USD", rig.start, rig.index[2], "ENTRY")
    assert after.reports["EUR_USD"]["unexpected_missing_minutes"] == 1
    assert after.reports["EUR_USD"]["expected_provider_gap_minutes"] == 1
    assert after.reports["EUR_USD"]["is_valid"] is False
    assert "available_at" not in json.dumps(after.physical_gaps())


def test_boundaries_are_real_and_only_available_after_source_close(rig):
    rig.repair(confirm_provider_missing=True)
    record = rig.records()[0]
    assert pd.Timestamp(record.before.timestamp) == rig.index[0]
    assert pd.Timestamp(record.after.timestamp) == rig.index[2]
    assert record.before.bid == rig.original.iloc[0].bid_c
    assert record.after.ask == rig.original.iloc[-1].ask_o
    assert record.boundaries_at(rig.index[1])["before"] is not None
    assert record.boundaries_at(rig.index[1])["after"] is None
    assert record.boundaries_at(rig.index[2])["after"] is None
    assert record.boundaries_at(rig.end)["after"] is not None
    broken = record.model_dump(mode="json")
    broken["after"]["available_at"] = rig.index[1].isoformat()
    with pytest.raises(ValidationError, match="cannot precede"):
        ProviderMissingConfirmation.model_validate(broken)


def test_repeated_same_proof_is_not_an_independent_confirmation(rig):
    rig.repair(confirm_provider_missing=True)
    broken = rig.records()[0].model_dump(mode="json")
    broken["checks"][1] = broken["checks"][0]
    with pytest.raises(ValidationError, match="independent"):
        ProviderMissingConfirmation.model_validate(broken)


def test_market_closure_cannot_become_a_provider_gap(rig):
    rig.start, rig.end = pd.Timestamp("2025-04-26T12:00Z"), pd.Timestamp("2025-04-26T12:03Z")
    result = rig.repair(confirm_provider_missing=True)
    assert result["missing_before"] == result["provider_confirmed_missing"] == 0
    assert rig.calls == []


def test_confirmation_changes_revision_but_not_prices(rig):
    before = rig.provider.data_revision(["EUR_USD"], rig.start, rig.end)
    rig.repair(confirm_provider_missing=True)
    assert rig.provider.data_revision(["EUR_USD"], rig.start, rig.end) != before
    pd.testing.assert_frame_equal(rig.provider.get("EUR_USD", rig.start, rig.end), rig.original)


def test_reports_keep_provider_gaps_distinct_from_closures(rig):
    rig.repair(confirm_provider_missing=True)
    report = validate_market_data(rig.original, "EUR_USD", rig.start, rig.end)
    rig.provider.classify_report("EUR_USD", report, rig.start, rig.end)
    summary = gap_report._build_summary(report, rig.start, rig.end)
    assert summary["total_minutes"] == summary["provider_confirmed_missing"] == 1
    assert summary["unconfirmed_missing_minutes"] == 0
    assert summary["provider_confirmed_gaps"][0]["classification"] == "PROVIDER_CONFIRMED_MISSING"
    assert report.expected_market_closed_minutes == 0
    assert rig.provider.status()[0]["provider_confirmed_missing"] == 1


def test_repair_cli_distinguishes_confirmed_gaps_from_repairable_failure(monkeypatch):
    monkeypatch.setattr("sys.argv", ["download_history.py", "--from", "2025-04-28", "--to", "2025-04-28", "--repair-gaps"])
    monkeypatch.setattr(DukascopyProvider, "repair_gaps", lambda *a, **k: {"EUR_USD": {"unconfirmed_missing_after": 0, "provider_confirmed_missing": 1}})
    assert download_history.main() == 2


def test_current_utc_day_cannot_be_confirmed_absent(rig):
    with pytest.raises(ValueError, match="completed UTC day"):
        rig.repair(confirm_provider_missing=True, now=rig.end)
    assert rig.calls == []
