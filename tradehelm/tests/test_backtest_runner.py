from datetime import date, datetime, timezone

from tradehelm.historical.cache import HistoricalCache
from tradehelm.historical.backtest_runner import BacktestRunner
from tradehelm.persistence.db import create_session_factory
from tradehelm.trading_engine.types import Bar


def test_backtest_run_created_from_cached_data(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'bt.db'}")
    cache = HistoricalCache(session_factory, cache_dir=str(tmp_path / "cache"))
    bars = [
        Bar(ts=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc), symbol="AAPL", open=100, high=101, low=99, close=100.5, volume=1000),
        Bar(ts=datetime(2026, 1, 2, 14, 35, tzinfo=timezone.utc), symbol="AAPL", open=100.5, high=101, low=100, close=100.2, volume=900),
    ]
    cache.write_dataset(
        provider="twelvedata",
        symbol="AAPL",
        interval="5min",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
        adjusted=True,
        bars=bars,
        splits=[],
        dividends=[],
    )
    runner = BacktestRunner(session_factory, cache)
    result = runner.run("twelvedata", ["AAPL"], "2026-01-01", "2026-01-03", "5min", True)
    assert result["status"] == "COMPLETED"
    runs = runner.list_runs()
    assert runs
    assert runs[0]["provider"] == "twelvedata"


def test_backtest_cached_data_does_not_require_live_provider(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'bt2.db'}")
    cache = HistoricalCache(session_factory, cache_dir=str(tmp_path / "cache"))
    cache.write_dataset(
        provider="twelvedata",
        symbol="MSFT",
        interval="5min",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
        adjusted=False,
        bars=[Bar(ts=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc), symbol="MSFT", open=1, high=1, low=1, close=1, volume=1)],
        splits=[],
        dividends=[],
    )
    runner = BacktestRunner(session_factory, cache)
    result = runner.run("twelvedata", ["MSFT"], "2026-01-01", "2026-01-03", "5min", False)
    assert result["status"] == "COMPLETED"
