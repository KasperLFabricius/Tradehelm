from datetime import date, datetime, timezone

import pytest

from tradehelm.historical.adjustments import apply_corporate_action_adjustments
from tradehelm.historical.cache import HistoricalCache
from tradehelm.historical.interfaces import DividendEvent, SplitEvent
from tradehelm.historical.intervals import ensure_supported_interval, interval_to_timedelta, supported_intervals
from tradehelm.historical.service import HistoricalRequest, HistoricalService
from tradehelm.historical.twelvedata import TwelveDataHistoricalProvider
from tradehelm.persistence.db import create_session_factory
from tradehelm.trading_engine.types import Bar


class FakeProvider(TwelveDataHistoricalProvider):
    def __init__(self):
        super().__init__(api_key="fake")

    def fetch_bars(self, symbol, interval, start_date, end_date):
        return [
            Bar(ts=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc), symbol=symbol, open=10, high=12, low=9, close=11, volume=100),
            Bar(ts=datetime(2026, 1, 2, 14, 35, tzinfo=timezone.utc), symbol=symbol, open=11, high=13, low=10, close=12, volume=110),
            Bar(ts=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc), symbol=symbol, open=10, high=12, low=9, close=11, volume=100),
        ]

    def fetch_splits(self, symbol, start_date, end_date):
        return [SplitEvent(symbol=symbol, ex_date=date(2026, 1, 3), ratio_from=1, ratio_to=2)]

    def fetch_dividends(self, symbol, start_date, end_date):
        return [DividendEvent(symbol=symbol, ex_date=date(2026, 1, 4), amount=0.5)]


def test_supported_interval_validation_and_timedelta():
    assert ensure_supported_interval("1min") == "1min"
    assert interval_to_timedelta("30min").total_seconds() == 1800
    assert "5min" in supported_intervals()
    with pytest.raises(ValueError):
        ensure_supported_interval("2min")


def test_cache_key_includes_interval(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'k.db'}")
    cache = HistoricalCache(session_factory, cache_dir=str(tmp_path / "cache"))
    key_5 = cache.make_cache_key("twelvedata", "AAPL", "5min", date(2026, 1, 1), date(2026, 1, 10), True)
    key_1 = cache.make_cache_key("twelvedata", "AAPL", "1min", date(2026, 1, 1), date(2026, 1, 10), True)
    assert key_5 != key_1


def test_cache_key_rejects_unsupported_interval(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'k2.db'}")
    cache = HistoricalCache(session_factory, cache_dir=str(tmp_path / "cache"))
    with pytest.raises(ValueError):
        cache.make_cache_key("twelvedata", "AAPL", "2min", date(2026, 1, 1), date(2026, 1, 10), True)


def test_write_dataset_stores_canonical_interval_in_manifest(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'manifest.db'}")
    cache = HistoricalCache(session_factory, cache_dir=str(tmp_path / "cache"))
    cache.write_dataset(
        provider="twelvedata",
        symbol="AAPL",
        interval=" 5min ",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 2),
        adjusted=False,
        bars=[Bar(ts=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc), symbol="AAPL", open=1, high=1, low=1, close=1, volume=1)],
        splits=[],
        dividends=[],
    )
    dataset = cache.find_dataset("twelvedata", "AAPL", "5min", date(2026, 1, 1), date(2026, 1, 2), False)
    assert dataset is not None
    assert dataset.interval == "5min"


def test_adjustment_pipeline_split_behavior():
    bars = [Bar(ts=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc), symbol="AAPL", open=100, high=101, low=99, close=100, volume=1)]
    adjusted = apply_corporate_action_adjustments(
        bars,
        splits=[SplitEvent(symbol="AAPL", ex_date=date(2026, 1, 3), ratio_from=1, ratio_to=2)],
        dividends=[],
        apply_dividends=False,
    )
    assert adjusted[0].close == 50


def test_fetch_assembly_dedup_and_sort(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'f.db'}")
    cache = HistoricalCache(session_factory, cache_dir=str(tmp_path / "cache"))
    service = HistoricalService(cache=cache, provider=FakeProvider())
    result = service.fetch_and_cache(
        HistoricalRequest(
            symbols=["aapl"],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 6),
            interval="15min",
            adjusted=False,
        ),
        use_existing=False,
    )
    assert result["downloaded"][0]["bars"] == 2
    dataset = cache.find_dataset("twelvedata", "AAPL", "15min", date(2026, 1, 1), date(2026, 1, 6), False)
    assert dataset is not None


def test_api_validation_symbol_and_date(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'v.db'}")
    cache = HistoricalCache(session_factory, cache_dir=str(tmp_path / "cache"))
    service = HistoricalService(cache=cache, provider=FakeProvider())
    bad_req = HistoricalRequest(
        symbols=["AAPL", "BAD1"],
        start_date=date(2026, 1, 10),
        end_date=date(2026, 1, 1),
        interval="1min",
        adjusted=False,
    )
    with pytest.raises(Exception):
        service.validate_request(bad_req)


def test_chunked_fetch_assembly_logic_is_interval_aware():
    provider = TwelveDataHistoricalProvider(api_key="fake", bars_chunk_days=1)
    calls = []

    def fake_request(path, params):
        if path == "time_series":
            calls.append((params["start_date"], params["end_date"]))
            start = datetime.fromisoformat(params["start_date"])
            return {
                "values": [
                    {
                        "datetime": start.isoformat(sep=" "),
                        "open": "1",
                        "high": "1",
                        "low": "1",
                        "close": "1",
                        "volume": "1",
                    }
                ]
            }
        return {"splits": [], "dividends": []}

    provider._request = fake_request  # type: ignore[method-assign]
    provider.fetch_bars("AAPL", "1h", date(2026, 1, 1), date(2026, 1, 3))
    assert len(calls) >= 2
    first_next = datetime.fromisoformat(calls[1][0])
    first_end = datetime.fromisoformat(calls[0][1])
    assert first_end - first_next == interval_to_timedelta("1h")
