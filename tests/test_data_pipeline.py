from __future__ import annotations

from datetime import date
from pathlib import Path
from threading import Lock

import numpy as np
import pandas as pd
import pytest

from app.config import SessionDef
from app.data.base import normalize_canonical
from app.data.csv_import import DukascopyCsvImporter
from app.data.dukascopy import DukascopyProvider, combine_bid_ask, decode_candle_payload
from app.data.resampler import resample_market_data
from app.data.validation import validate_market_data
from app.sessions import build_session_instances


def side_frame(index: pd.DatetimeIndex, offset: float = 0.0, volume: float = 1.0) -> pd.DataFrame:
    price = 1.1 + np.arange(len(index)) * 0.00001 + offset
    return pd.DataFrame(
        {
            "open": price,
            "high": price + 0.0003,
            "low": price - 0.0002,
            "close": price + 0.0001,
            "volume": volume,
        },
        index=index,
    )


def canonical_frame(index: pd.DatetimeIndex) -> pd.DataFrame:
    return combine_bid_ask(side_frame(index), side_frame(index, 0.0002, 2.0))


def test_decode_dukascopy_payload_and_utc_timestamp():
    payload = {
        "timestamp": 1_704_153_600_000,
        "multiplier": 0.00001,
        "shift": 60_000,
        "open": 1.1,
        "high": 1.1002,
        "low": 1.0998,
        "close": 1.1001,
        "times": [0, 1],
        "opens": [0, 1],
        "highs": [0, 2],
        "lows": [0, -1],
        "closes": [0, 1],
        "volumes": [0.01, 0.02],
    }
    frame = decode_candle_payload(payload)
    assert str(frame.index.tz) == "UTC"
    assert frame.index[1] - frame.index[0] == pd.Timedelta(minutes=1)
    assert frame.iloc[1].open == pytest.approx(1.10001)
    assert frame.iloc[1].high == pytest.approx(1.10022)
    assert frame.volume.tolist() == [10_000, 20_000]


def test_bid_ask_normalization_and_mid_calculation():
    index = pd.date_range("2024-01-02", periods=3, freq="1min", tz="UTC")
    frame = combine_bid_ask(side_frame(index), side_frame(index, 0.0002, 2.0))
    assert frame.mid_o.iloc[0] == pytest.approx((frame.bid_o.iloc[0] + frame.ask_o.iloc[0]) / 2)
    assert frame.mid_h.iloc[2] == pytest.approx((frame.bid_h.iloc[2] + frame.ask_h.iloc[2]) / 2)
    assert frame.volume.equals(frame.bid_volume)
    assert frame.ask_volume.iloc[0] == 2.0


def test_duplicate_removal_is_deterministic():
    timestamp = pd.Timestamp("2024-01-02T12:00:00Z")
    frame = pd.DataFrame(
        {
            "timestamp": [timestamp, timestamp],
            "mid_o": [1.0, 2.0],
            "mid_h": [1.1, 2.1],
            "mid_l": [0.9, 1.9],
            "mid_c": [1.0, 2.0],
        }
    )
    normalized = normalize_canonical(frame)
    assert len(normalized) == 1
    assert normalized.iloc[0].mid_c == 2.0


def test_resampling_m5_h1_h2_h4_and_volume():
    index = pd.date_range("2024-01-02", periods=240, freq="1min", tz="UTC")
    frame = canonical_frame(index)
    m5 = resample_market_data(frame, "M5", "UTC")
    h1 = resample_market_data(frame, "H1", "UTC")
    h2 = resample_market_data(frame, "H2", "UTC")
    h4 = resample_market_data(frame, "H4", "UTC")
    assert (len(m5), len(h1), len(h2), len(h4)) == (48, 4, 2, 1)
    assert m5.iloc[0].bid_o == pytest.approx(frame.iloc[0].bid_o)
    assert m5.iloc[0].bid_h == pytest.approx(frame.iloc[:5].bid_h.max())
    assert m5.iloc[0].bid_l == pytest.approx(frame.iloc[:5].bid_l.min())
    assert m5.iloc[0].bid_c == pytest.approx(frame.iloc[4].bid_c)
    assert m5.iloc[0].bid_volume == 5.0
    assert m5.iloc[0].ask_volume == 10.0


def test_gap_detection_and_weekend_classification():
    monday = pd.DatetimeIndex(
        [pd.Timestamp("2024-01-08T12:00:00Z"), pd.Timestamp("2024-01-08T12:02:00Z")]
    )
    report = validate_market_data(
        canonical_frame(monday),
        "EUR_USD",
        pd.Timestamp("2024-01-08T12:00:00Z"),
        pd.Timestamp("2024-01-08T12:03:00Z"),
    )
    assert report.unexpected_missing_minutes == 1
    assert report.unexpected_gaps[0].start == "2024-01-08T12:01:00+00:00"

    weekend_start = pd.Timestamp("2024-01-06T00:00:00Z")
    weekend_end = pd.Timestamp("2024-01-07T00:00:00Z")
    weekend = validate_market_data(pd.DataFrame(), "EUR_USD", weekend_start, weekend_end)
    assert weekend.unexpected_missing_minutes == 0
    assert weekend.expected_market_closed_minutes == 1440
    assert weekend.expected_holiday_minutes == 0

    holiday_start = pd.Timestamp("2024-01-08T12:00:00Z")
    holiday_end = holiday_start + pd.Timedelta(hours=1)
    holiday = validate_market_data(
        pd.DataFrame(),
        "EUR_USD",
        holiday_start,
        holiday_end,
        [{"from": int(holiday_start.timestamp() * 1000), "till": int(holiday_end.timestamp() * 1000)}],
    )
    assert holiday.unexpected_missing_minutes == 0
    assert holiday.expected_market_closed_minutes == 0
    assert holiday.expected_holiday_minutes == 60


def test_validation_rejects_partial_nan_prices_and_invalid_volume():
    index = pd.date_range("2024-01-08T12:00:00Z", periods=2, freq="1min")
    frame = canonical_frame(index)
    frame.loc[index[0], "bid_h"] = np.nan
    frame.loc[index[1], "ask_volume"] = -1

    report = validate_market_data(frame, "EUR_USD", index[0], index[-1] + pd.Timedelta(minutes=1))

    assert report.invalid_value_rows == 2


def test_europe_paris_dst_sessions_remain_wall_clock_aligned():
    definitions = [SessionDef("BLUE", pd.Timestamp("07:00").time(), pd.Timestamp("11:00").time())]
    instances = build_session_instances(
        pd.Timestamp("2024-03-29T00:00:00Z").to_pydatetime(),
        pd.Timestamp("2024-04-02T00:00:00Z").to_pydatetime(),
        definitions,
        "Europe/Paris",
    )
    by_date = {item.trade_date.isoformat(): item for item in instances}
    assert by_date["2024-03-29"].start.hour == 6
    assert by_date["2024-04-01"].start.hour == 5
    assert by_date["2024-03-29"].end - by_date["2024-03-29"].start == pd.Timedelta(hours=4)
    assert by_date["2024-04-01"].end - by_date["2024-04-01"].start == pd.Timedelta(hours=4)


def test_h2_resampling_is_deterministic_across_spring_dst():
    index = pd.date_range("2024-03-30 23:00", periods=360, freq="1min", tz="UTC")
    h2 = resample_market_data(canonical_frame(index), "H2", "Europe/Paris")
    local_index = h2.index.tz_convert("Europe/Paris")

    assert [timestamp.hour for timestamp in local_index] == [0, 3, 5]
    assert list(h2.index.to_series().diff().dropna()) == [
        pd.Timedelta(hours=2),
        pd.Timedelta(hours=2),
    ]


class FakeDukascopyClient:
    def __init__(self):
        self.calls: list[tuple[str, date, str]] = []
        self.lock = Lock()

    def instrument(self, provider_symbol: str):
        return {"code": provider_symbol, "holidays": []}

    def fetch_day(self, provider_symbol: str, day: date, side: str, now=None):
        with self.lock:
            self.calls.append((provider_symbol, day, side))
        index = pd.date_range(pd.Timestamp(day, tz="UTC"), periods=3, freq="1min")
        return side_frame(index, 0.0002 if side == "ASK" else 0.0, 2.0 if side == "ASK" else 1.0)


def test_cache_is_idempotent_and_partial_download_only_fetches_missing_days(tmp_path: Path):
    client = FakeDukascopyClient()
    provider = DukascopyProvider(tmp_path, validation_mode="permissive", client=client, workers=2)
    now = pd.Timestamp("2024-01-10T12:00:00Z")
    provider.download(["EUR_USD"], pd.Timestamp("2024-01-02T00:00:00Z"), pd.Timestamp("2024-01-04T00:00:00Z"), now=now)
    assert len(client.calls) == 4
    revision = provider.data_revision(
        ["EUR_USD"], pd.Timestamp("2024-01-02T00:00:00Z"), pd.Timestamp("2024-01-04T00:00:00Z")
    )
    provider.download(["EUR_USD"], pd.Timestamp("2024-01-02T00:00:00Z"), pd.Timestamp("2024-01-04T00:00:00Z"), now=now)
    assert len(client.calls) == 4
    assert revision == provider.data_revision(
        ["EUR_USD"], pd.Timestamp("2024-01-02T00:00:00Z"), pd.Timestamp("2024-01-04T00:00:00Z")
    )
    provider.download(["EUR_USD"], pd.Timestamp("2024-01-02T00:00:00Z"), pd.Timestamp("2024-01-05T00:00:00Z"), now=now)
    assert len(client.calls) == 6
    assert {call[1] for call in client.calls[-2:]} == {date(2024, 1, 4)}
    cached = provider.store.read("EUR_USD", pd.Timestamp("2024-01-02T00:00:00Z"), pd.Timestamp("2024-01-05T00:00:00Z"))
    assert len(cached) == 9
    assert provider.store.instruments() == ["EUR_USD"]


def test_manual_bid_and_ask_csv_share_the_canonical_cache(tmp_path: Path):
    bid_path = tmp_path / "EUR-USD_1_Minute_BID_UTC_2024-01-02.csv"
    ask_path = tmp_path / "EUR-USD_1_Minute_ASK_UTC_2024-01-02.csv"
    content = "UTC,Open,High,Low,Close,Volume\n2024-01-02T00:00:00+00:00,{o},{h},{l},{c},1\n"
    bid_path.write_text(content.format(o=1.1, h=1.1002, l=1.0998, c=1.1001), encoding="utf-8")
    ask_path.write_text(content.format(o=1.1002, h=1.1004, l=1.1, c=1.1003), encoding="utf-8")
    provider = DukascopyProvider(tmp_path, validation_mode="permissive")
    importer = DukascopyCsvImporter(provider)
    first = importer.import_file(bid_path, now=pd.Timestamp("2024-01-03T00:00:00Z"))
    second = importer.import_file(ask_path, now=pd.Timestamp("2024-01-03T00:00:00Z"))
    assert first["pending_counterpart_months"] == ["2024-01"]
    assert second["materialized_rows"] == 1
    cached = provider.store.read(
        "EUR_USD", pd.Timestamp("2024-01-02T00:00:00Z"), pd.Timestamp("2024-01-02T00:01:00Z")
    )
    assert len(cached) == 1
    assert cached.iloc[0].mid_o == pytest.approx(1.1001)
