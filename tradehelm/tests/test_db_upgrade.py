from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from tradehelm.control_api.app import create_app
from tradehelm.persistence.db import create_session_factory


def _create_old_schema_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE positions (
            symbol TEXT PRIMARY KEY,
            qty INTEGER,
            avg_entry REAL,
            last_price REAL,
            realized_pnl REAL
        );
        CREATE TABLE closed_trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            entry_price REAL,
            exit_price REAL,
            qty INTEGER,
            pnl REAL
        );
        CREATE TABLE replay_sessions (
            id INTEGER PRIMARY KEY,
            dataset TEXT,
            started_at DATETIME,
            status TEXT
        );
        """
    )
    cur.execute("INSERT INTO positions(symbol, qty, avg_entry, last_price, realized_pnl) VALUES ('DEMO', 1, 100.0, 101.0, 2.0)")
    cur.execute("INSERT INTO closed_trades(id, symbol, entry_price, exit_price, qty, pnl) VALUES (1, 'DEMO', 100.0, 102.0, 1, 2.0)")
    cur.execute("INSERT INTO replay_sessions(id, dataset, started_at, status) VALUES (1, 'old.csv', '2026-01-01 14:30:00', 'LOADED')")
    conn.commit()
    conn.close()


def _columns(db_path: Path, table: str) -> set[str]:
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    cols = {row[1] for row in cur.fetchall()}
    conn.close()
    return cols


def test_sqlite_upgrade_adds_missing_columns_and_preserves_rows(tmp_path):
    db = tmp_path / "legacy.db"
    _create_old_schema_db(db)

    sf = create_session_factory(f"sqlite:///{db}")

    assert {"opened_at", "cumulative_fees"}.issubset(_columns(db, "positions"))
    assert {"side", "entry_ts", "exit_ts", "gross_pnl", "fees", "net_pnl"}.issubset(_columns(db, "closed_trades"))
    assert {"loaded_at", "started_at", "completed_at"}.issubset(_columns(db, "replay_sessions"))

    with sf() as s:
        row = s.execute(text("SELECT symbol, cumulative_fees FROM positions WHERE symbol='DEMO'"))
        symbol, cumulative_fees = row.one()
        assert symbol == "DEMO"
        assert float(cumulative_fees) == 0.0

        trade = s.execute(text("SELECT side, gross_pnl, fees, net_pnl FROM closed_trades WHERE id=1")).one()
        assert trade[0] == "LONG"
        assert float(trade[1]) == 2.0
        assert float(trade[2]) == 0.0
        assert float(trade[3]) == 2.0

        replay = s.execute(text("SELECT dataset, loaded_at FROM replay_sessions WHERE id=1")).one()
        assert replay[0] == "old.csv"
        assert replay[1] is not None


def test_sqlite_upgrade_creates_decisions_table_when_missing(tmp_path):
    db = tmp_path / "legacy_decisions.db"
    _create_old_schema_db(db)

    create_session_factory(f"sqlite:///{db}")

    conn = sqlite3.connect(db)
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    conn.close()
    assert "decisions" in names


def test_sqlite_upgrade_adds_decisions_action_column_when_missing(tmp_path):
    db = tmp_path / "legacy_decision_action.db"
    conn = sqlite3.connect(db)
    conn.execute(
        """
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY,
            ts DATETIME,
            strategy_id TEXT,
            symbol TEXT,
            side TEXT,
            qty INTEGER,
            accepted INTEGER,
            reason TEXT,
            mode TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO decisions(id, strategy_id, symbol, side, qty, accepted, reason, mode) VALUES (1, 'orb', 'AAPL', 'BUY', 1, 1, 'legacy', 'PAPER')"
    )
    conn.commit()
    conn.close()

    sf = create_session_factory(f"sqlite:///{db}")
    assert "action" in _columns(db, "decisions")
    with sf() as s:
        action = s.execute(text("SELECT action FROM decisions WHERE id=1")).scalar_one()
        assert action == "UNKNOWN"


def test_app_start_and_analytics_endpoints_work_after_upgrade(tmp_path):
    db = tmp_path / "legacy_api.db"
    _create_old_schema_db(db)

    app = create_app(f"sqlite:///{db}")
    with TestClient(app) as client:
        summary = client.get("/analytics/summary")
        trades = client.get("/analytics/trades")
        sessions = client.get("/analytics/sessions")

        assert summary.status_code == 200
        assert trades.status_code == 200
        assert sessions.status_code == 200
        assert summary.json()["total_closed_trades"] >= 1


def test_sqlite_upgrade_adds_backtest_run_snapshot_and_artifact_columns(tmp_path):
    db = tmp_path / "legacy_bt_upgrade.db"
    conn = sqlite3.connect(db)
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

    create_session_factory(f"sqlite:///{db}")
    cols = _columns(db, "backtest_runs")
    assert {"config_json", "equity_curve_json", "symbol_summary_json", "decision_summary_json", "trades_json", "decisions_json"}.issubset(cols)
