"""Database setup and SQLAlchemy models."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker


class Base(DeclarativeBase):
    """SQLAlchemy base class."""


class StateTransition(Base):
    __tablename__ = "state_transitions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    mode: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(255), default="")


class EventLog(Base):
    __tablename__ = "event_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    level: Mapped[str] = mapped_column(String(16))
    event_type: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(String(500))


class DecisionRecord(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    strategy_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(16), default="UNKNOWN")
    accepted: Mapped[int] = mapped_column(Integer, default=0)
    reason: Mapped[str] = mapped_column(String(255), default="")
    mode: Mapped[str] = mapped_column(String(32), default="PAPER")


class OrderRecord(Base):
    __tablename__ = "orders"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column(Integer)
    filled_qty: Mapped[int] = mapped_column(Integer, default=0)
    order_type: Mapped[str] = mapped_column(String(16))
    limit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(24))


class FillRecord(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8))
    qty: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float)


class PositionRecord(Base):
    __tablename__ = "positions"
    symbol: Mapped[str] = mapped_column(String(16), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer)
    avg_entry: Mapped[float] = mapped_column(Float)
    last_price: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cumulative_fees: Mapped[float] = mapped_column(Float, default=0.0)


class ClosedTradeRecord(Base):
    __tablename__ = "closed_trades"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    symbol: Mapped[str] = mapped_column(String(16))
    side: Mapped[str] = mapped_column(String(8), default="LONG")
    entry_price: Mapped[float] = mapped_column(Float)
    exit_price: Mapped[float] = mapped_column(Float)
    qty: Mapped[int] = mapped_column(Integer)
    entry_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    exit_ts: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    gross_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    fees: Mapped[float] = mapped_column(Float, default=0.0)
    net_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    pnl: Mapped[float] = mapped_column(Float)


class ReplaySessionRecord(Base):
    __tablename__ = "replay_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dataset: Mapped[str] = mapped_column(String(255))
    loaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="LOADED")


class AppConfigRecord(Base):
    __tablename__ = "app_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    version: Mapped[str] = mapped_column(String(32), default="v1")
    payload_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class RuntimeMetadataRecord(Base):
    __tablename__ = "runtime_metadata"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    payload_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class HistoricalDatasetRecord(Base):
    __tablename__ = "historical_datasets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(128), unique=True)
    provider: Mapped[str] = mapped_column(String(32))
    symbol: Mapped[str] = mapped_column(String(16))
    interval: Mapped[str] = mapped_column(String(16))
    start_date: Mapped[str] = mapped_column(String(16))
    end_date: Mapped[str] = mapped_column(String(16))
    adjusted: Mapped[int] = mapped_column(Integer, default=0)
    bars_path: Mapped[str] = mapped_column(String(500))
    splits_path: Mapped[str] = mapped_column(String(500))
    dividends_path: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BacktestRunRecord(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(32))
    symbols_csv: Mapped[str] = mapped_column(String(500))
    interval: Mapped[str] = mapped_column(String(16))
    start_date: Mapped[str] = mapped_column(String(16))
    end_date: Mapped[str] = mapped_column(String(16))
    adjusted: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(24), default="PENDING")
    dataset_keys_csv: Mapped[str] = mapped_column(Text, default="")
    summary_json: Mapped[str] = mapped_column(Text, default="{}")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    trades_json: Mapped[str] = mapped_column(Text, default="[]")
    decisions_json: Mapped[str] = mapped_column(Text, default="[]")
    equity_curve_json: Mapped[str] = mapped_column(Text, default="[]")
    symbol_summary_json: Mapped[str] = mapped_column(Text, default="[]")
    decision_summary_json: Mapped[str] = mapped_column(Text, default="{}")
    strategy_summary_json: Mapped[str] = mapped_column(Text, default="[]")
    trade_timeline_json: Mapped[str] = mapped_column(Text, default="[]")
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class BacktestJobRecord(Base):
    __tablename__ = "backtest_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(24), default="QUEUED")
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_json: Mapped[str] = mapped_column(Text, default="{}")
    snapshot_json: Mapped[str] = mapped_column(Text, default="{}")
    progress_json: Mapped[str] = mapped_column(Text, default="{}")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BacktestEventRecord(Base):
    __tablename__ = "backtest_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(Integer)
    run_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    message: Mapped[str] = mapped_column(String(500))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


def _sqlite_table_columns(conn, table: str) -> set[str]:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).all()
    return {str(row[1]) for row in rows}


def _upgrade_sqlite_schema(engine: Engine) -> None:
    """Idempotent SQLite-only compatibility upgrade for older local DBs."""
    if engine.dialect.name != "sqlite":
        return

    required_columns = {
        "positions": {
            "opened_at": "ALTER TABLE positions ADD COLUMN opened_at DATETIME",
            "cumulative_fees": "ALTER TABLE positions ADD COLUMN cumulative_fees REAL DEFAULT 0.0",
        },
        "closed_trades": {
            "side": "ALTER TABLE closed_trades ADD COLUMN side TEXT DEFAULT 'LONG'",
            "entry_ts": "ALTER TABLE closed_trades ADD COLUMN entry_ts DATETIME",
            "exit_ts": "ALTER TABLE closed_trades ADD COLUMN exit_ts DATETIME",
            "gross_pnl": "ALTER TABLE closed_trades ADD COLUMN gross_pnl REAL DEFAULT 0.0",
            "fees": "ALTER TABLE closed_trades ADD COLUMN fees REAL DEFAULT 0.0",
            "net_pnl": "ALTER TABLE closed_trades ADD COLUMN net_pnl REAL DEFAULT 0.0",
        },
        "replay_sessions": {
            "loaded_at": "ALTER TABLE replay_sessions ADD COLUMN loaded_at DATETIME",
            "started_at": "ALTER TABLE replay_sessions ADD COLUMN started_at DATETIME",
            "completed_at": "ALTER TABLE replay_sessions ADD COLUMN completed_at DATETIME",
        },
        "decisions": {
            "action": "ALTER TABLE decisions ADD COLUMN action TEXT DEFAULT 'UNKNOWN'",
        },
        "backtest_runs": {
            "config_json": "ALTER TABLE backtest_runs ADD COLUMN config_json TEXT DEFAULT '{}'",
            "trades_json": "ALTER TABLE backtest_runs ADD COLUMN trades_json TEXT DEFAULT '[]'",
            "decisions_json": "ALTER TABLE backtest_runs ADD COLUMN decisions_json TEXT DEFAULT '[]'",
            "equity_curve_json": "ALTER TABLE backtest_runs ADD COLUMN equity_curve_json TEXT DEFAULT '[]'",
            "symbol_summary_json": "ALTER TABLE backtest_runs ADD COLUMN symbol_summary_json TEXT DEFAULT '[]'",
            "decision_summary_json": "ALTER TABLE backtest_runs ADD COLUMN decision_summary_json TEXT DEFAULT '{}'",
            "strategy_summary_json": "ALTER TABLE backtest_runs ADD COLUMN strategy_summary_json TEXT DEFAULT '[]'",
            "trade_timeline_json": "ALTER TABLE backtest_runs ADD COLUMN trade_timeline_json TEXT DEFAULT '[]'",
        },
        "backtest_jobs": {
            "run_id": "ALTER TABLE backtest_jobs ADD COLUMN run_id INTEGER",
            "request_json": "ALTER TABLE backtest_jobs ADD COLUMN request_json TEXT DEFAULT '{}'",
            "snapshot_json": "ALTER TABLE backtest_jobs ADD COLUMN snapshot_json TEXT DEFAULT '{}'",
            "progress_json": "ALTER TABLE backtest_jobs ADD COLUMN progress_json TEXT DEFAULT '{}'",
            "error_message": "ALTER TABLE backtest_jobs ADD COLUMN error_message TEXT",
            "cancel_requested": "ALTER TABLE backtest_jobs ADD COLUMN cancel_requested INTEGER DEFAULT 0",
            "started_at": "ALTER TABLE backtest_jobs ADD COLUMN started_at DATETIME",
            "completed_at": "ALTER TABLE backtest_jobs ADD COLUMN completed_at DATETIME",
        },
        "backtest_events": {
            "run_id": "ALTER TABLE backtest_events ADD COLUMN run_id INTEGER",
            "payload_json": "ALTER TABLE backtest_events ADD COLUMN payload_json TEXT DEFAULT '{}'",
        },
    }

    with engine.begin() as conn:
        for table, columns in required_columns.items():
            existing = _sqlite_table_columns(conn, table)
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(ddl))

        # Backfill conservative defaults for rows that pre-date these columns.
        conn.execute(text("UPDATE positions SET cumulative_fees = COALESCE(cumulative_fees, 0.0)"))
        conn.execute(text("UPDATE closed_trades SET side = COALESCE(NULLIF(side, ''), 'LONG')"))
        conn.execute(text("UPDATE closed_trades SET gross_pnl = COALESCE(gross_pnl, pnl)"))
        conn.execute(text("UPDATE closed_trades SET fees = COALESCE(fees, 0.0)"))
        conn.execute(text("UPDATE closed_trades SET net_pnl = COALESCE(net_pnl, pnl)"))
        conn.execute(text("UPDATE replay_sessions SET loaded_at = COALESCE(loaded_at, started_at, CURRENT_TIMESTAMP)"))
        conn.execute(text("UPDATE replay_sessions SET status = COALESCE(NULLIF(status, ''), 'LOADED')"))
        conn.execute(text("UPDATE decisions SET action = COALESCE(NULLIF(action, ''), 'UNKNOWN')"))
        conn.execute(text("UPDATE backtest_runs SET config_json = COALESCE(config_json, '{}')"))
        conn.execute(text("UPDATE backtest_runs SET trades_json = COALESCE(trades_json, '[]')"))
        conn.execute(text("UPDATE backtest_runs SET decisions_json = COALESCE(decisions_json, '[]')"))
        conn.execute(text("UPDATE backtest_runs SET equity_curve_json = COALESCE(equity_curve_json, '[]')"))
        conn.execute(text("UPDATE backtest_runs SET symbol_summary_json = COALESCE(symbol_summary_json, '[]')"))
        conn.execute(text("UPDATE backtest_runs SET decision_summary_json = COALESCE(decision_summary_json, '{}')"))
        conn.execute(text("UPDATE backtest_runs SET strategy_summary_json = COALESCE(strategy_summary_json, '[]')"))
        conn.execute(text("UPDATE backtest_runs SET trade_timeline_json = COALESCE(trade_timeline_json, '[]')"))


def create_session_factory(db_url: str = "sqlite:///tradehelm.db") -> sessionmaker:
    """Create DB schema and return session factory."""
    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(engine)
    _upgrade_sqlite_schema(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
