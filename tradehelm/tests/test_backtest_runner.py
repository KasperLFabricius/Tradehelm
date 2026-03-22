from datetime import date, datetime, timezone
import sqlite3

from tradehelm.config.models import AppConfig
from tradehelm.control_api.app import build_historical_stack
from tradehelm.historical.cache import HistoricalCache
from tradehelm.historical.backtest_runner import BacktestRunner
from tradehelm.persistence.db import ClosedTradeRecord, PositionRecord, create_session_factory
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
    runner = BacktestRunner(session_factory, cache, AppConfig())
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
    runner = BacktestRunner(session_factory, cache, AppConfig())
    result = runner.run("twelvedata", ["MSFT"], "2026-01-01", "2026-01-03", "5min", False)
    assert result["status"] == "COMPLETED"


def test_backtest_summary_is_isolated_from_existing_records(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'bt3.db'}")
    cache = HistoricalCache(session_factory, cache_dir=str(tmp_path / "cache"))
    with session_factory() as session:
        session.add(
            ClosedTradeRecord(
                symbol="OLD",
                side="LONG",
                entry_price=1.0,
                exit_price=2.0,
                qty=10,
                gross_pnl=10.0,
                fees=0.0,
                net_pnl=10.0,
                pnl=10.0,
            )
        )
        session.commit()

    cache.write_dataset(
        provider="twelvedata",
        symbol="AAPL",
        interval="5min",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
        adjusted=True,
        bars=[Bar(ts=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc), symbol="AAPL", open=1, high=1, low=1, close=1, volume=1)],
        splits=[],
        dividends=[],
    )
    runner = BacktestRunner(session_factory, cache, AppConfig())
    run1 = runner.run("twelvedata", ["AAPL"], "2026-01-01", "2026-01-03", "5min", True)
    run2 = runner.run("twelvedata", ["AAPL"], "2026-01-01", "2026-01-03", "5min", True)
    assert run1["summary"]["total_closed_trades"] == 0
    assert run2["summary"]["total_closed_trades"] == 0


def test_backtest_starts_flat_even_if_main_db_has_open_position(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'bt4.db'}")
    cache = HistoricalCache(session_factory, cache_dir=str(tmp_path / "cache"))
    with session_factory() as session:
        session.add(PositionRecord(symbol="AAPL", qty=50, avg_entry=10.0, last_price=10.5, realized_pnl=0.0, opened_at=None, cumulative_fees=0.0))
        session.commit()
    cache.write_dataset(
        provider="twelvedata",
        symbol="AAPL",
        interval="5min",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
        adjusted=False,
        bars=[Bar(ts=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc), symbol="AAPL", open=10, high=10, low=10, close=10, volume=1)],
        splits=[],
        dividends=[],
    )
    runner = BacktestRunner(session_factory, cache, AppConfig())
    result = runner.run("twelvedata", ["AAPL"], "2026-01-01", "2026-01-03", "5min", False)
    assert result["status"] == "COMPLETED"
    assert result["summary"]["total_closed_trades"] == 0


def test_build_historical_stack_uses_configured_cache_and_api_key_env(tmp_path, monkeypatch):
    cfg = AppConfig.model_validate(
        {
            "historical": {
                "cache_dir": str(tmp_path / "custom_cache"),
                "api_key_env": "MY_TD_KEY",
            }
        }
    )
    monkeypatch.setenv("MY_TD_KEY", "configured-key")
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'bt5.db'}")
    cache, service, _ = build_historical_stack(session_factory, cfg)
    assert str(cache.cache_dir).endswith("custom_cache")
    assert service.provider._require_api_key() == "configured-key"


def test_sqlite_upgrade_adds_backtest_artifact_columns(tmp_path):
    db_path = tmp_path / "legacy_backtest.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE backtest_runs (
            id INTEGER PRIMARY KEY,
            provider TEXT,
            symbols_csv TEXT,
            interval TEXT,
            start_date TEXT,
            end_date TEXT,
            adjusted INTEGER,
            status TEXT,
            dataset_keys_csv TEXT,
            summary_json TEXT,
            started_at DATETIME,
            completed_at DATETIME,
            created_at DATETIME
        )
        """
    )
    conn.commit()
    conn.close()
    create_session_factory(f"sqlite:///{db_path}")
    conn = sqlite3.connect(db_path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(backtest_runs)").fetchall()}
    conn.close()
    assert "trades_json" in columns
    assert "decisions_json" in columns
