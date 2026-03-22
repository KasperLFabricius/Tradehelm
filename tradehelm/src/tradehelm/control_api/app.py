"""FastAPI control plane for TradeHelm."""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from tradehelm.config.models import AppConfig
from tradehelm.persistence.db import create_session_factory
from tradehelm.strategies.noop import NoOpStrategy
from tradehelm.strategies.orb import OpeningRangeBreakoutStrategy
from tradehelm.trading_engine.engine import TradingEngine
from tradehelm.trading_engine.types import BotMode

session_factory = create_session_factory("sqlite:///tradehelm.db")
engine = TradingEngine(session_factory, AppConfig(), [NoOpStrategy(), OpeningRangeBreakoutStrategy()])
app = FastAPI(title="TradeHelm Control API")


class ModeRequest(BaseModel):
    mode: BotMode


class ConfigRequest(BaseModel):
    config: AppConfig


class ReplayLoadRequest(BaseModel):
    path: str


@app.get("/health")
def health() -> dict:
    return {"ok": True}


@app.get("/state")
def get_state() -> dict:
    return engine.state()


@app.post("/state/mode")
def set_mode(req: ModeRequest) -> dict:
    return {"mode": engine.set_mode(req.mode).value}


@app.post("/state/halt")
def halt() -> dict:
    return {"mode": engine.set_mode(BotMode.HALTED, reason="operator_halt").value}


@app.post("/state/kill")
def kill() -> dict:
    return {"mode": engine.set_mode(BotMode.KILL_SWITCH, reason="operator_kill").value}


@app.get("/orders")
def orders() -> list[dict]:
    return engine.orders()


@app.get("/fills")
def fills() -> list[dict]:
    return engine.fills()


@app.get("/positions")
def positions() -> list[dict]:
    return engine.positions()


@app.get("/trades")
def trades() -> list[dict]:
    return engine.trades()


@app.get("/logs")
def logs() -> list[dict]:
    return engine.logs()


@app.get("/strategies")
def strategies() -> list[dict]:
    return [{"strategy_id": k, "enabled": v.enabled} for k, v in engine.strategies.items()]


@app.post("/strategies/{strategy_id}/enable")
def enable(strategy_id: str) -> dict:
    engine.strategies[strategy_id].enabled = True
    return {"strategy_id": strategy_id, "enabled": True}


@app.post("/strategies/{strategy_id}/disable")
def disable(strategy_id: str) -> dict:
    engine.strategies[strategy_id].enabled = False
    return {"strategy_id": strategy_id, "enabled": False}


@app.get("/config")
def get_config() -> dict:
    return engine.config.model_dump()


@app.post("/config")
def set_config(req: ConfigRequest) -> dict:
    engine.config = req.config
    return {"updated": True}


@app.post("/replay/load")
def replay_load(req: ReplayLoadRequest) -> dict:
    engine.load_replay(req.path)
    return {"loaded": req.path}


@app.post("/replay/start")
def replay_start() -> dict:
    engine.run_replay()
    return {"started": True}


@app.post("/replay/stop")
def replay_stop() -> dict:
    engine.set_mode(BotMode.STOPPED, reason="replay_stop")
    return {"stopped": True}
