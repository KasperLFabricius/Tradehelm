"""FastAPI control plane for TradeHelm."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import text

from tradehelm.config.models import AppConfig
from tradehelm.historical.backtest_runner import BacktestRunner
from tradehelm.historical.cache import HistoricalCache
from tradehelm.historical.interfaces import SUPPORTED_INTERVAL
from tradehelm.historical.service import HistoricalRequest, HistoricalService
from tradehelm.historical.twelvedata import TwelveDataHistoricalProvider
from tradehelm.persistence.db import create_session_factory
from tradehelm.persistence.state_store import PersistedStateStore
from tradehelm.strategies.noop import NoOpStrategy
from tradehelm.strategies.orb import OpeningRangeBreakoutStrategy
from tradehelm.strategies.vwap import VwapContinuationStrategy
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


class HistoricalFetchRequest(BaseModel):
    symbols: list[str]
    start_date: date
    end_date: date
    interval: str = SUPPORTED_INTERVAL
    adjusted: bool = True
    use_existing_cache: bool = True


class BacktestLaunchRequest(BaseModel):
    symbols: list[str]
    start_date: date
    end_date: date
    interval: str = SUPPORTED_INTERVAL
    adjusted: bool = True


def build_historical_stack(session_factory, config: AppConfig) -> tuple[HistoricalCache, HistoricalService, BacktestRunner]:
    cache = HistoricalCache(session_factory, cache_dir=config.historical.cache_dir)
    provider = TwelveDataHistoricalProvider(
        api_key=os.getenv(config.historical.api_key_env),
        api_key_env=config.historical.api_key_env,
    )
    service = HistoricalService(cache=cache, provider=provider)
    runner = BacktestRunner(session_factory, cache, config)
    return cache, service, runner


def create_engine_instance(db_url: str = "sqlite:///tradehelm.db") -> TradingEngine:
    session_factory = create_session_factory(db_url)
    state_store = PersistedStateStore(session_factory)
    config = AppConfig()
    strategies = [
        NoOpStrategy(),
        OpeningRangeBreakoutStrategy(config.strategies.orb),
        VwapContinuationStrategy(config.strategies.vwap),
    ]
    return TradingEngine(session_factory, config, strategies, state_store=state_store)


def create_app(db_url: str = "sqlite:///tradehelm.db") -> FastAPI:
    engine = create_engine_instance(db_url)
    cache: HistoricalCache | None = None
    historical_service: HistoricalService | None = None
    backtest_runner: BacktestRunner | None = None

    def rebuild_historical_services() -> None:
        nonlocal cache, historical_service, backtest_runner
        cache, historical_service, backtest_runner = build_historical_stack(engine.session_factory, engine.config)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        engine.startup()
        rebuild_historical_services()
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
        items = []
        for sid, state in engine.strategies.items():
            items.append(
                {
                    "strategy_id": sid,
                    "enabled": state.enabled,
                    "status": state.strategy.status(),
                }
            )
        return items

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
        result = engine.apply_config(config)
        rebuild_historical_services()
        return result

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

    @app.post("/historical/fetch")
    def historical_fetch(req: HistoricalFetchRequest):
        assert historical_service is not None
        try:
            result = historical_service.fetch_and_cache(
                HistoricalRequest(
                    symbols=req.symbols,
                    start_date=req.start_date,
                    end_date=req.end_date,
                    interval=req.interval,
                    adjusted=req.adjusted,
                ),
                use_existing=req.use_existing_cache,
            )
            return result
        except Exception as exc:
            status, payload = historical_service.map_error(exc)
            return JSONResponse(status_code=status, content=ApiError(error=payload).model_dump())

    @app.post("/historical/prepare")
    def historical_prepare(req: HistoricalFetchRequest):
        return historical_fetch(req)

    @app.get("/historical/cache")
    def historical_cache() -> list[dict]:
        assert cache is not None
        return cache.list_cache_files()

    @app.get("/historical/datasets")
    def historical_datasets() -> list[dict]:
        assert cache is not None
        return cache.list_datasets()

    @app.post("/backtests/run")
    def backtests_run(req: BacktestLaunchRequest):
        assert historical_service is not None
        assert backtest_runner is not None
        try:
            historical_service.validate_request(
                HistoricalRequest(
                    symbols=req.symbols,
                    start_date=req.start_date,
                    end_date=req.end_date,
                    interval=req.interval,
                    adjusted=req.adjusted,
                )
            )
            symbols = sorted({s.strip().upper() for s in req.symbols if s.strip()})
            return backtest_runner.run(
                provider=historical_service.provider.name,
                symbols=symbols,
                start_date=req.start_date.isoformat(),
                end_date=req.end_date.isoformat(),
                interval=req.interval,
                adjusted=req.adjusted,
            )
        except ValueError as exc:
            if str(exc).startswith("no_cached_dataset_available:"):
                symbol = str(exc).split(":", 1)[1]
                payload = ApiError(
                    error={"code": "no_cached_dataset_available", "message": f"No cached dataset available for {symbol}."}
                ).model_dump()
                return JSONResponse(status_code=400, content=payload)
            raise
        except Exception as exc:
            status, payload = historical_service.map_error(exc)
            return JSONResponse(status_code=status, content=ApiError(error=payload).model_dump())

    @app.get("/backtests/runs")
    def backtests_runs() -> list[dict]:
        assert backtest_runner is not None
        return backtest_runner.list_runs()

    @app.get("/backtests/{run_id}")
    def backtests_run_detail(run_id: int):
        assert backtest_runner is not None
        payload = backtest_runner.get_run(run_id)
        if payload is None:
            return JSONResponse(
                status_code=404,
                content=ApiError(error={"code": "backtest_run_not_found", "message": "Backtest run not found."}).model_dump(),
            )
        return payload

    return app


app = create_app()
