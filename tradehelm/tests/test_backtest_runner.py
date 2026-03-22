from datetime import date, datetime, timezone
import json
import sqlite3
from pathlib import Path

import tradehelm.historical.backtest_runner as backtest_runner_module
from tradehelm.config.models import AppConfig
from tradehelm.control_api.app import build_historical_stack
from tradehelm.historical.backtest_runner import BacktestRunner
from tradehelm.historical.cache import HistoricalCache
from tradehelm.historical.run_analysis import RunAnalysisService
from tradehelm.persistence.db import BacktestRunRecord, ClosedTradeRecord, PositionRecord, create_session_factory
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
    assert result["equity_curve"]
    runs = runner.list_runs()
    assert runs[0]["provider"] == "twelvedata"


def test_backtest_snapshot_persists_interval_and_strategy_config(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'snapshot.db'}")
    cache = HistoricalCache(session_factory, cache_dir=str(tmp_path / "cache"))
    cache.write_dataset(
        provider="twelvedata",
        symbol="AAPL",
        interval="15min",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
        adjusted=False,
        bars=[Bar(ts=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc), symbol="AAPL", open=1, high=1, low=1, close=1, volume=1)],
        splits=[],
        dividends=[],
    )
    cfg = AppConfig.model_validate({"strategies": {"orb": {"opening_range_bars": 7}, "vwap": {"pullback_threshold": 0.21}}})
    runner = BacktestRunner(session_factory, cache, cfg)
    result = runner.run("twelvedata", ["AAPL"], "2026-01-01", "2026-01-03", "15min", False)
    with session_factory() as session:
        row = session.get(BacktestRunRecord, result["run_id"])
        assert row is not None
        config = json.loads(row.config_json)
    assert config["interval"] == "15min"
    assert config["strategies"]["orb"]["opening_range_bars"] == 7
    assert config["strategies"]["vwap"]["pullback_threshold"] == 0.21


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
    assert "config_json" in columns
    assert "equity_curve_json" in columns


def test_run_analysis_symbol_and_decision_summary_for_multi_symbol():
    svc = RunAnalysisService()
    trades = [
        {"id": 1, "symbol": "AAPL", "net_pnl": 10.0, "fees": 1.0, "exit_ts": "2026-01-01T14:35:00+00:00"},
        {"id": 2, "symbol": "MSFT", "net_pnl": -5.0, "fees": 0.5, "exit_ts": "2026-01-01T14:40:00+00:00"},
        {"id": 3, "symbol": "AAPL", "net_pnl": 4.0, "fees": 0.2, "exit_ts": "2026-01-01T14:45:00+00:00"},
    ]
    decisions = [
        {"strategy_id": "orb", "accepted": True, "reason": "accepted_entry"},
        {"strategy_id": "orb", "accepted": False, "reason": "risk_rejection"},
        {"strategy_id": "vwap", "accepted": False, "reason": "risk_rejection"},
    ]
    artifacts = svc.build_run_artifacts(trades, decisions)
    assert artifacts["equity_curve"]
    aapl = [row for row in artifacts["symbol_summary"] if row["symbol"] == "AAPL"][0]
    assert aapl["trades"] == 2
    assert aapl["net_pnl"] == 14.0
    assert artifacts["decision_summary"]["by_reason"]["risk_rejection"] == 2


def test_compare_runs_returns_metrics(tmp_path):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'compare.db'}")
    cache = HistoricalCache(session_factory, cache_dir=str(tmp_path / "cache"))
    runner = BacktestRunner(session_factory, cache, AppConfig())
    with session_factory() as session:
        session.add(
            BacktestRunRecord(
                provider="twelvedata",
                symbols_csv="AAPL",
                interval="5min",
                start_date="2026-01-01",
                end_date="2026-01-02",
                adjusted=1,
                status="COMPLETED",
                dataset_keys_csv="abc",
                summary_json=json.dumps({"net_realized_pnl": 5, "gross_realized_pnl": 6, "win_rate": 0.5, "total_closed_trades": 2, "total_fees_paid": 1.0, "best_trade": 4, "worst_trade": -2}),
                symbol_summary_json=json.dumps([{"symbol": "AAPL", "trades": 2}]),
            )
        )
        session.add(
            BacktestRunRecord(
                provider="twelvedata",
                symbols_csv="MSFT",
                interval="15min",
                start_date="2026-01-01",
                end_date="2026-01-02",
                adjusted=0,
                status="COMPLETED",
                dataset_keys_csv="def",
                summary_json=json.dumps({"net_realized_pnl": -1, "gross_realized_pnl": 0, "win_rate": 0.0, "total_closed_trades": 1, "total_fees_paid": 1.0, "best_trade": -1, "worst_trade": -1}),
                symbol_summary_json=json.dumps([{"symbol": "MSFT", "trades": 1}]),
            )
        )
        session.commit()
    comparison = runner.compare_runs([1, 2])
    assert len(comparison["runs"]) == 2
    assert comparison["runs"][0]["trade_count"] >= 1


def test_backtest_uses_windows_safe_temp_db_path(tmp_path, monkeypatch):
    session_factory = create_session_factory(f"sqlite:///{tmp_path / 'windows_safe.db'}")
    cache = HistoricalCache(session_factory, cache_dir=str(tmp_path / "cache"))
    cache.write_dataset(
        provider="twelvedata",
        symbol="AAPL",
        interval="5min",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
        adjusted=False,
        bars=[Bar(ts=datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc), symbol="AAPL", open=1, high=1, low=1, close=1, volume=1)],
        splits=[],
        dividends=[],
    )

    observed_run_db_urls: list[str] = []
    original_create_session_factory = backtest_runner_module.create_session_factory

    def capture_create_session_factory(db_url: str):
        observed_run_db_urls.append(db_url)
        return original_create_session_factory(db_url)

    monkeypatch.setattr(backtest_runner_module, "create_session_factory", capture_create_session_factory)

    runner = BacktestRunner(session_factory, cache, AppConfig())
    result = runner.run("twelvedata", ["AAPL"], "2026-01-01", "2026-01-03", "5min", False)

    assert result["status"] == "COMPLETED"
    assert observed_run_db_urls
    run_db_path = Path(observed_run_db_urls[0].replace("sqlite:///", ""))
    assert run_db_path.name == "run.db"
    assert not run_db_path.parent.exists()
