from datetime import date, datetime, timezone

from tradehelm.historical.adjustments import apply_corporate_action_adjustments
from tradehelm.historical.cache import HistoricalCache
from tradehelm.historical.interfaces import DividendEvent, SplitEvent
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


def test_cache_key_is_deterministic(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'k.db'}")
    cache = HistoricalCache(session_factory, cache_dir=str(tmp_path / "cache"))
    key_a = cache.make_cache_key("twelvedata", "AAPL", "5min", date(2026, 1, 1), date(2026, 1, 10), True)
    key_b = cache.make_cache_key("twelvedata", "AAPL", "5min", date(2026, 1, 1), date(2026, 1, 10), True)
    key_c = cache.make_cache_key("twelvedata", "AAPL", "5min", date(2026, 1, 1), date(2026, 1, 10), False)
    assert key_a == key_b
    assert key_a != key_c


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
            interval="5min",
            adjusted=False,
        ),
        use_existing=False,
    )
    assert result["downloaded"][0]["bars"] == 2
    dataset = cache.find_dataset("twelvedata", "AAPL", "5min", date(2026, 1, 1), date(2026, 1, 6), False)
    assert dataset is not None
    loaded = cache.load_bars(dataset.cache_key)
    assert len(loaded) == 2
    assert loaded[0].ts <= loaded[1].ts


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
    try:
        service.validate_request(bad_req)
        assert False
    except Exception as exc:
        assert "invalid_symbols" in getattr(exc, "code", "") or "unsupported_interval" in getattr(exc, "code", "")


def test_chunked_fetch_assembly_logic():
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
    bars = provider.fetch_bars("AAPL", "5min", date(2026, 1, 1), date(2026, 1, 3))
    assert len(calls) >= 2
    assert bars == sorted(bars, key=lambda b: b.ts)
