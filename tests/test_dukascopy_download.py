from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from hashlib import sha256
import time

import httpx
import pandas as pd
import pytest
from pydantic import ValidationError

from app.data.base import DataCoverageError, empty_canonical_frame
from app.data.dukascopy import DukascopyClient, DukascopyError, DukascopyProvider, RequestPolicy, combine_bid_ask
from scripts import download_history, gap_report


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        value = 0.0

        def sleep(self, seconds):
            self.value += seconds

    clock = Clock()
    monkeypatch.setattr("app.data.dukascopy.time.monotonic", lambda: clock.value)
    monkeypatch.setattr("app.data.dukascopy.time.sleep", clock.sleep)
    return clock


@pytest.fixture
def http_client():
    clients = []

    def make(handler, **kwargs):
        client = DukascopyClient(**kwargs)
        client.client.close()
        client.client = httpx.Client(base_url="https://example.test", transport=httpx.MockTransport(handler))
        clients.append(client)
        return client

    yield make
    for client in clients:
        client.close()


@pytest.mark.parametrize("header,delay", [("3", 3.0), ("invalid", 1.0), ("-1", 1.0), ("0", 1.0)])
def test_retry_after_seconds_and_invalid_header_fallback(clock, http_client, header, delay):
    attempts = []

    def respond(request):
        attempts.append(clock.value)
        return httpx.Response(429, headers={"Retry-After": header}) if len(attempts) == 1 else httpx.Response(200, json={"ok": True})

    client = http_client(respond)
    assert client.instrument("EUR-USD") == {"ok": True}
    assert attempts == [0.0, delay]


def test_retry_after_http_date(clock, http_client, monkeypatch):
    now = datetime(2024, 1, 2, tzinfo=timezone.utc)

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr("app.data.dukascopy.datetime", FixedDatetime)
    attempts = []

    def respond(request):
        attempts.append(clock.value)
        return httpx.Response(503, headers={"Retry-After": format_datetime(now + timedelta(seconds=7))}) if len(attempts) == 1 else httpx.Response(200, json={})

    http_client(respond).instrument("EUR-USD")
    assert attempts == [0.0, 7.0]


def test_network_backoff_is_exponential_and_bounded(clock, http_client):
    attempts = []

    def respond(request):
        attempts.append(clock.value)
        raise httpx.ConnectError("offline", request=request)

    with pytest.raises(DukascopyError, match="after 4 attempts"):
        http_client(respond, request_interval_seconds=0).instrument("EUR-USD")
    assert attempts == [0.0, 0.5, 1.5, 3.5]


def test_permanent_http_error_is_not_retried(clock, http_client):
    attempts = []

    def respond(request):
        attempts.append(clock.value)
        return httpx.Response(404)

    with pytest.raises(DukascopyError, match="HTTP 404"):
        http_client(respond).instrument("missing")
    assert attempts == [0.0]


def test_exhausted_retry_cooldown_applies_to_next_request(clock, http_client):
    attempts = []

    def respond(request):
        attempts.append(clock.value)
        return httpx.Response(429, headers={"Retry-After": "5"}) if len(attempts) == 1 else httpx.Response(200, json={})

    client = http_client(respond, retries=1)
    with pytest.raises(DukascopyError):
        client.instrument("EUR-USD")
    client.instrument("GBP-USD")
    assert attempts == [0.0, 5.0]


def test_workers_share_one_rate_limit_and_one_inflight_request(http_client):
    # Yield in the transport so overlapping HTTP calls would be observable.
    real_sleep = time.sleep
    active = 0
    maximum = 0
    starts = []

    def respond(request):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        starts.append(time.monotonic())
        real_sleep(0.005)
        active -= 1
        return httpx.Response(200, json={})

    client = http_client(respond, request_interval_seconds=0.02)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(client.instrument, ["EUR-USD"] * 8))
    assert maximum == 1
    assert all(b - a >= 0.019 for a, b in zip(starts, starts[1:]))


def test_lazy_client_is_shared_even_with_cached_metadata(provider, monkeypatch):
    created = []

    def create():
        time.sleep(0.005)
        client = object()
        created.append(client)
        return client

    monkeypatch.setattr("app.data.dukascopy.DukascopyClient", create)
    with ThreadPoolExecutor(max_workers=8) as pool:
        clients = list(pool.map(lambda _: provider._network_client(), range(8)))
    assert len(created) == 1
    assert all(client is created[0] for client in clients)


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf")])
def test_rate_limit_configuration_rejects_invalid_values(value):
    with pytest.raises(ValidationError):
        RequestPolicy(request_interval_seconds=value)


def candles(index, offset=0.0):
    bid = pd.DataFrame({"open": 1.1 + offset, "high": 1.101 + offset,
                        "low": 1.099 + offset, "close": 1.1 + offset, "volume": 1.0}, index=index)
    ask = bid.copy()
    ask[["open", "high", "low", "close"]] += 0.0002
    return combine_bid_ask(bid, ask)


def seed(provider, frame, complete=True):
    for day, part in frame.groupby(frame.index.date):
        provider.store.write_day("EUR_USD", day, part, "EUR-USD", complete, pd.Timestamp("2024-02-10T00:00Z"))


@pytest.fixture
def provider(tmp_path):
    provider = DukascopyProvider(tmp_path, validation_mode="permissive")
    provider.store.save_metadata("EUR_USD", {"holidays": []})
    return provider


def test_repair_only_missing_minutes_preserves_existing_and_is_idempotent(provider, monkeypatch):
    index = pd.date_range("2024-01-02T00:00Z", periods=6, freq="min")
    original = candles(index.delete([2, 5]))
    seed(provider, original)  # Old completed_days must not hide real gaps.
    calls = []

    def fetch(instrument, day, now):
        calls.append(day)
        return candles(index, offset=0.2)

    monkeypatch.setattr(provider, "_fetch_day", fetch)
    result = provider.repair_gaps(["EUR_USD"], index[1], index[4], now=index[-1] + pd.Timedelta(days=1))["EUR_USD"]
    assert result == {"downloaded_days": 1, "added_rows": 1, "missing_before": 1, "missing_after": 0,
                      "provider_confirmed_missing": 0, "unconfirmed_missing_after": 0}
    stored = provider.store.read("EUR_USD", index[0], index[-1] + pd.Timedelta(minutes=1))
    pd.testing.assert_frame_equal(stored.loc[original.index], original, check_freq=False)
    assert index[5] not in stored.index  # Another gap outside the requested window.
    assert stored.loc[index[2], "bid_o"] == pytest.approx(1.3)
    path = provider.store.partition_path("EUR_USD", "2024-01")
    digest = sha256(path.read_bytes()).hexdigest()
    again = provider.repair_gaps(["EUR_USD"], index[1], index[4], now=index[-1] + pd.Timedelta(days=1))["EUR_USD"]
    assert again["downloaded_days"] == 0
    assert len(calls) == 1
    assert sha256(path.read_bytes()).hexdigest() == digest


def test_repair_resumes_after_interrupted_manifest_write(provider, monkeypatch):
    index = pd.date_range("2024-01-02T00:00Z", periods=3, freq="min")
    seed(provider, candles(index.delete(1)))
    calls = []

    def fetch(*args):
        calls.append(args)
        return candles(index)

    monkeypatch.setattr(provider, "_fetch_day", fetch)
    manifest_path = provider.store.manifest_path("EUR_USD")
    before = manifest_path.read_bytes()
    with monkeypatch.context() as patch:
        def fail(*args):
            raise OSError("interrupted manifest write")
        patch.setattr(provider.store, "_atomic_json", fail)
        with pytest.raises(OSError, match="interrupted"):
            provider.repair_gaps(["EUR_USD"], index[0], index[-1] + pd.Timedelta(minutes=1))
    assert manifest_path.read_bytes() == before
    result = provider.repair_gaps(["EUR_USD"], index[0], index[-1] + pd.Timedelta(minutes=1))["EUR_USD"]
    assert result["missing_after"] == 0
    assert len(calls) == 1
    manifest = provider.store.load_manifest("EUR_USD")
    assert manifest["partitions"]["2024-01"]["sha256"] == sha256(provider.store.partition_path("EUR_USD", "2024-01").read_bytes()).hexdigest()


def test_repair_resumes_only_remaining_days_after_network_failure(provider, monkeypatch):
    index = pd.date_range("2024-01-02T23:59Z", periods=2, freq="min")
    calls = []

    def fetch(instrument, day, now):
        calls.append(day)
        if len(calls) == 2:
            raise DukascopyError("network exhausted")
        return candles(index[index.date == day])

    monkeypatch.setattr(provider, "_fetch_day", fetch)
    with pytest.raises(DukascopyError):
        provider.repair_gaps(["EUR_USD"], index[0], index[-1] + pd.Timedelta(minutes=1))
    assert len(provider.store.read("EUR_USD", index[0], index[-1] + pd.Timedelta(minutes=1))) == 1
    result = provider.repair_gaps(["EUR_USD"], index[0], index[-1] + pd.Timedelta(minutes=1))["EUR_USD"]
    assert result["missing_after"] == 0
    assert calls == [index[0].date(), index[1].date(), index[1].date()]


def test_empty_reply_keeps_gap_visible_and_existing_prices(provider, monkeypatch):
    index = pd.date_range("2024-01-02T00:00Z", periods=3, freq="min")
    original = candles(index.delete(1))
    seed(provider, original)
    monkeypatch.setattr(provider, "_fetch_day", lambda *args: empty_canonical_frame())
    result = provider.repair_gaps(["EUR_USD"], index[0], index[-1] + pd.Timedelta(minutes=1))["EUR_USD"]
    assert result["missing_after"] == 1
    pd.testing.assert_frame_equal(provider.store.read("EUR_USD", index[0], index[-1] + pd.Timedelta(minutes=1)), original)


@pytest.mark.parametrize("repair", [False, True])
def test_corrupt_reply_never_changes_cache(provider, monkeypatch, repair):
    index = pd.date_range("2024-01-02T00:00Z", periods=3, freq="min")
    seed(provider, candles(index.delete(1)))
    path = provider.store.partition_path("EUR_USD", "2024-01")
    before = path.read_bytes()
    bad = candles(index)
    bad.loc[index[1], "bid_volume"] = -1
    monkeypatch.setattr(provider, "_fetch_day", lambda *args: bad)
    with pytest.raises(DataCoverageError):
        if repair:
            provider.repair_gaps(["EUR_USD"], index[0], index[-1] + pd.Timedelta(minutes=1))
        else:
            provider.download(["EUR_USD"], index[0], index[-1] + pd.Timedelta(minutes=1), force=True)
    assert path.read_bytes() == before


def test_force_download_does_not_replace_valid_observations(provider, monkeypatch):
    index = pd.date_range("2024-01-02T00:00Z", periods=3, freq="min")
    original = candles(index)
    seed(provider, original)
    monkeypatch.setattr(provider, "_fetch_day", lambda *args: candles(index[:1], offset=0.2))
    provider.download(["EUR_USD"], index[0], index[-1] + pd.Timedelta(minutes=1), force=True)
    pd.testing.assert_frame_equal(provider.store.read("EUR_USD", index[0], index[-1] + pd.Timedelta(minutes=1)), original, check_freq=False)


@pytest.mark.parametrize("instrument,start,end", [
    ("EUR_USD", "2024-01-06T12:00Z", "2024-01-06T13:00Z"),
    ("DXY", "2024-01-02T22:00Z", "2024-01-03T01:00Z"),
])
def test_scheduled_closures_never_trigger_repair(provider, monkeypatch, instrument, start, end):
    provider.store.save_metadata(instrument, {"holidays": []})
    def unexpected(*args):
        pytest.fail("No download expected during scheduled closure")
    monkeypatch.setattr(provider, "_fetch_day", unexpected)
    assert provider.repair_gaps([instrument], start, end)[instrument]["downloaded_days"] == 0


def test_repair_respects_holidays_and_current_candle(provider, monkeypatch):
    index = pd.date_range("2024-01-02T12:00Z", periods=4, freq="min")
    provider.store.save_metadata("EUR_USD", {"holidays": [{"from": int(index[0].timestamp() * 1000), "till": int(index[1].timestamp() * 1000)}]})
    monkeypatch.setattr(provider, "_fetch_day", lambda *args: candles(index))
    report = provider.repair_gaps(["EUR_USD"], index[0], index[-1] + pd.Timedelta(minutes=1), now=index[2] + pd.Timedelta(seconds=30))["EUR_USD"]
    assert report["added_rows"] == 1
    stored = provider.store.read("EUR_USD", index[0], index[-1] + pd.Timedelta(minutes=1))
    assert stored.index.tolist() == [index[1]]


def test_repair_cli_returns_nonzero_when_gaps_remain(monkeypatch):
    monkeypatch.setattr("sys.argv", ["download_history.py", "--from", "2024-01-02", "--to", "2024-01-02", "--repair-gaps"])
    monkeypatch.setattr(DukascopyProvider, "repair_gaps", lambda *args, **kwargs: {"EUR_USD": {"missing_after": 1, "unconfirmed_missing_after": 1, "provider_confirmed_missing": 0}})
    assert download_history.main() == 1


def test_gap_report_uses_cached_holidays_and_summary_output(provider, monkeypatch, capsys):
    start, end = pd.Timestamp("2024-01-02T00:00Z"), pd.Timestamp("2024-01-03T00:00Z")
    provider.store.save_metadata("EUR_USD", {"holidays": [{"from": int(start.timestamp() * 1000), "till": int(end.timestamp() * 1000)}]})
    monkeypatch.setattr(gap_report, "DukascopyProvider", lambda *args, **kwargs: provider)
    monkeypatch.setattr("sys.argv", ["gap_report.py", "--from", str(start), "--to", str(end)])
    assert gap_report.main() == 0
    assert "Unexpected missing minutes total: 0" in capsys.readouterr().out

    provider.store.save_metadata("EUR_USD", {"holidays": []})
    gap_report.main()
    output = capsys.readouterr().out
    assert "Unexpected missing minutes total: 1440" in output
    assert len(output.splitlines()) < 20
