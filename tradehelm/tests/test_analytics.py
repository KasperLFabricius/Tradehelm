from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select

from tradehelm.config.models import AppConfig, RiskConfig
from tradehelm.control_api.app import create_app
from tradehelm.persistence.db import ClosedTradeRecord, DecisionRecord, FillRecord, create_session_factory
from tradehelm.trading_engine.engine import TradingEngine
from tradehelm.trading_engine.types import Bar, OrderSide, OrderType


def _client_for(db_path: Path) -> TestClient:
    app = create_app(f"sqlite:///{db_path}")
    return TestClient(app)


def test_replay_session_metadata_load_start_complete(tmp_path):
    dataset = tmp_path / "demo.csv"
    dataset.write_text("timestamp,symbol,open,high,low,close,volume\n2026-01-01T14:30:00Z,DEMO,1,1,1,1,100\n")

    with _client_for(tmp_path / "session.db") as client:
        assert client.post("/replay/load", json={"path": str(dataset)}).status_code == 200
        assert client.post("/state/mode", json={"mode": "PAPER"}).status_code == 200
        assert client.post("/replay/start").status_code == 200

        for _ in range(20):
            sessions = client.get("/analytics/sessions").json()
            if sessions and sessions[0]["completed_at"] is not None:
                break
            time.sleep(0.1)

        latest = client.get("/analytics/sessions").json()[0]
        assert latest["status"] in {"COMPLETED", "STOPPED"}
        assert latest["loaded_at"] is not None
        assert latest["started_at"] is not None
        assert latest["completed_at"] is not None


def test_summary_metrics_and_fees_consistent_from_closed_trades():
    sf = create_session_factory("sqlite:///:memory:")
    engine = TradingEngine(sf, AppConfig(risk=RiskConfig(max_trades_per_day=10)), [])
    ts = datetime.fromisoformat("2026-01-01T14:30:00+00:00")

    engine.broker.submit_order("DEMO", OrderSide.BUY, 1, OrderType.MARKET)
    engine.broker.on_bar(Bar(ts, "DEMO", 100, 100, 100, 100, 1))
    engine.broker.submit_order("DEMO", OrderSide.SELL, 1, OrderType.MARKET)
    engine.broker.on_bar(Bar(ts.replace(minute=31), "DEMO", 101, 101, 101, 101, 1))

    summary = engine.analytics.summary()
    assert summary["total_closed_trades"] == 1
    assert summary["winners"] + summary["losers"] <= 1
    assert summary["total_fees_paid"] >= 0
    assert summary["realized_pnl_before_fees"] >= summary["realized_pnl_after_fees"]


def test_analytics_endpoints_and_decision_audit_and_reset(tmp_path):
    dataset = tmp_path / "demo.csv"
    dataset.write_text("timestamp,symbol,open,high,low,close,volume\n2026-01-01T14:30:00Z,DEMO,100,100,100,100,100\n")

    with _client_for(tmp_path / "api.db") as client:
        assert client.post("/replay/load", json={"path": str(dataset)}).status_code == 200
        assert client.post("/state/mode", json={"mode": "PAPER"}).status_code == 200
        assert client.post("/replay/start").status_code == 200
        time.sleep(0.2)

        assert client.get("/analytics/summary").status_code == 200
        assert isinstance(client.get("/analytics/trades").json(), list)
        assert isinstance(client.get("/analytics/sessions").json(), list)
        assert client.get("/analytics/fees").status_code == 200

        decisions = client.get("/analytics/decisions")
        assert decisions.status_code == 200
        assert isinstance(decisions.json(), list)

        bad_reset = client.post("/analytics/reset", json={"confirm": False})
        assert bad_reset.status_code == 400
        good_reset = client.post("/analytics/reset", json={"confirm": True})
        assert good_reset.status_code == 200


def test_decision_record_persists_rejection_reason():
    sf = create_session_factory("sqlite:///:memory:")
    engine = TradingEngine(sf, AppConfig(risk=RiskConfig(max_trades_per_day=0)), [])
    engine._record_decision("unit", "DEMO", "BUY", 1, accepted=False, reason="max_trades_per_day")

    with sf() as s:
        rows = s.scalars(select(DecisionRecord)).all()
        assert len(rows) == 1
        assert rows[0].accepted == 0
        assert "max_trades_per_day" in rows[0].reason


def test_closed_trade_records_enriched_fields_present():
    sf = create_session_factory("sqlite:///:memory:")
    engine = TradingEngine(sf, AppConfig(risk=RiskConfig(max_trades_per_day=10)), [])
    ts = datetime.fromisoformat("2026-01-01T14:30:00+00:00")

    engine.broker.submit_order("DEMO", OrderSide.BUY, 1, OrderType.MARKET)
    engine.broker.on_bar(Bar(ts, "DEMO", 100, 100, 100, 100, 1))
    engine.broker.submit_order("DEMO", OrderSide.SELL, 1, OrderType.MARKET)
    engine.broker.on_bar(Bar(ts.replace(minute=31), "DEMO", 101, 101, 101, 101, 1))

    with sf() as s:
        trade = s.scalars(select(ClosedTradeRecord)).first()
        fills = s.scalars(select(FillRecord)).all()
        assert trade is not None
        assert trade.entry_ts is not None
        assert trade.exit_ts is not None
        assert trade.fees >= 0
        assert trade.side in {"LONG", "SHORT"}
        assert len(fills) >= 2
