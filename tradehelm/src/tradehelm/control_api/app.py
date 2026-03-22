"""FastAPI control plane for TradeHelm."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import text

from tradehelm.config.models import AppConfig
from tradehelm.persistence.db import create_session_factory
from tradehelm.persistence.state_store import PersistedStateStore
from tradehelm.strategies.noop import NoOpStrategy
from tradehelm.strategies.orb import OpeningRangeBreakoutStrategy
from tradehelm.trading_engine.engine import TradingEngine
from tradehelm.trading_engine.errors import EngineError
from tradehelm.trading_engine.types import BotMode

logger = logging.getLogger(__name__)


class ModeRequest(BaseModel):
    mode: BotMode


class ConfigRequest(BaseModel):
    config: dict


class ReplayLoadRequest(BaseModel):
    path: str


class ApiError(BaseModel):
    error: dict[str, str]


class ResetRequest(BaseModel):
    confirm: bool = False


def create_engine_instance(db_url: str = "sqlite:///tradehelm.db") -> TradingEngine:
    session_factory = create_session_factory(db_url)
    state_store = PersistedStateStore(session_factory)
    return TradingEngine(session_factory, AppConfig(), [NoOpStrategy(), OpeningRangeBreakoutStrategy()], state_store=state_store)


def create_app(db_url: str = "sqlite:///tradehelm.db") -> FastAPI:
    engine = create_engine_instance(db_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        engine.startup()
        yield
        engine.shutdown()

    app = FastAPI(title="TradeHelm Control API", lifespan=lifespan)

    @app.exception_handler(EngineError)
    async def handle_engine_error(_: Request, exc: EngineError) -> JSONResponse:
        logger.warning("Engine error: %s", exc)
        status = {
            "invalid_mode_transition": 409,
            "replay_not_loaded": 409,
            "invalid_replay_path": 400,
            "strategy_not_found": 404,
        }.get(exc.code, 400)
        payload = ApiError(error={"code": exc.code, "message": str(exc)}).model_dump()
        return JSONResponse(status_code=status, content=payload)

    @app.exception_handler(ValidationError)
    async def handle_validation_error(_: Request, exc: ValidationError) -> JSONResponse:
        logger.warning("Validation error: %s", exc)
        payload = ApiError(error={"code": "invalid_payload", "message": "Invalid request payload."}).model_dump()
        return JSONResponse(status_code=422, content=payload)

    @app.get("/health")
    def health() -> dict:
        db_ok = True
        try:
            with engine.session_factory() as session:
                session.execute(text("SELECT 1"))
        except Exception:
            db_ok = False
        return {
            "ok": db_ok,
            "readiness": {
                "db_reachable": db_ok,
                "replay_loaded": engine.replay_loaded,
                "replay_running": engine.replay_running,
                "mode": engine.state_machine.mode.value,
                "active_config_loaded": engine.config is not None,
            },
        }

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
        return engine.set_strategy_enabled(strategy_id, True)

    @app.post("/strategies/{strategy_id}/disable")
    def disable(strategy_id: str) -> dict:
        return engine.set_strategy_enabled(strategy_id, False)

    @app.get("/config")
    def get_config() -> dict:
        return engine.config.model_dump()

    @app.post("/config")
    def set_config(req: ConfigRequest) -> dict:
        config = AppConfig.model_validate(req.config)
        return engine.apply_config(config)

    @app.post("/replay/load")
    def replay_load(req: ReplayLoadRequest) -> dict:
        engine.load_replay(req.path)
        return {"loaded": engine.replay_path}

    @app.post("/replay/start")
    def replay_start() -> dict:
        return engine.start_replay()

    @app.post("/replay/stop")
    def replay_stop() -> dict:
        engine.set_mode(BotMode.STOPPED, reason="replay_stop")
        return engine.stop_replay()

    @app.get("/analytics/summary")
    def analytics_summary() -> dict:
        return engine.analytics.summary()

    @app.get("/analytics/trades")
    def analytics_trades() -> list[dict]:
        return engine.analytics.trades()

    @app.get("/analytics/sessions")
    def analytics_sessions() -> list[dict]:
        return engine.analytics.sessions()

    @app.get("/analytics/fees")
    def analytics_fees() -> dict:
        return engine.analytics.fees()

    @app.get("/analytics/decisions")
    def analytics_decisions() -> list[dict]:
        return engine.decisions()

    @app.post("/analytics/reset")
    def analytics_reset(req: ResetRequest) -> dict:
        if not req.confirm:
            payload = ApiError(error={"code": "reset_confirmation_required", "message": "Reset requires confirm=true."}).model_dump()
            return JSONResponse(status_code=400, content=payload)
        return {"cleared": engine.reset_paper_records()}

    return app


app = create_app()
