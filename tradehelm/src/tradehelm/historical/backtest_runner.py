"""Cached-data backtest runner and local async job manager."""
from __future__ import annotations

import json
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

from tradehelm.analytics.service import AnalyticsService
from tradehelm.backtests.events import BacktestEventService
from tradehelm.backtests.models import BacktestRequest
from tradehelm.config.models import (
    AppConfig,
    FrictionConfig,
    GapOrbStrategyConfig,
    OrbStrategyConfig,
    RiskConfig,
    VwapMeanReversionStrategyConfig,
    VwapStrategyConfig,
)
from tradehelm.historical.cache import HistoricalCache
from tradehelm.historical.intervals import ensure_supported_interval
from tradehelm.historical.run_analysis import RunAnalysisService
from tradehelm.persistence.db import BacktestJobRecord, BacktestRunRecord, create_session_factory
from tradehelm.strategies.gap_orb import GapFilteredOrbStrategy
from tradehelm.strategies.noop import NoOpStrategy
from tradehelm.strategies.orb import OpeningRangeBreakoutStrategy
from tradehelm.strategies.vwap import VwapContinuationStrategy
from tradehelm.strategies.vwap_mean_reversion import VwapMeanReversionStrategy
from tradehelm.trading_engine.engine import TradingEngine
from tradehelm.trading_engine.types import BotMode


class BacktestRunner:
    def __init__(self, session_factory: sessionmaker, cache: HistoricalCache, app_config: AppConfig) -> None:
        self.main_session_factory = session_factory
        self.cache = cache
        self.app_config = app_config
        self.run_analysis = RunAnalysisService()
        self.events = BacktestEventService(session_factory)
        self._worker_thread: threading.Thread | None = None
        self._worker_lock = threading.Lock()
        self._worker_wakeup = threading.Event()

    def _request_dict(self, request: BacktestRequest) -> dict:
        payload = request.model_dump(mode="json")
        payload["symbols"] = sorted({s.strip().upper() for s in request.symbols if s.strip()})
        return payload

    def _resolve_config(self, request: BacktestRequest) -> AppConfig:
        cfg = self.app_config.model_copy(deep=True)
        known_strategies = {"orb", "vwap", "gap_orb", "vwap_mean_reversion"}
        unknown = sorted(set((request.strategy_params or {}).keys()) - known_strategies)
        if unknown:
            raise ValueError(f"unknown_strategy_override:{','.join(unknown)}")

        if request.friction_overrides:
            friction_payload = cfg.friction.model_dump()
            friction_payload.update(request.friction_overrides)
            cfg.friction = FrictionConfig.model_validate(friction_payload)
        if request.risk_overrides:
            risk_payload = cfg.risk.model_dump()
            risk_payload.update(request.risk_overrides)
            cfg.risk = RiskConfig.model_validate(risk_payload)

        enabled = set(request.enabled_strategies or [])
        for sid, patch in (request.strategy_params or {}).items():
            if sid == "orb":
                payload = cfg.strategies.orb.model_dump()
                payload.update(patch)
                cfg.strategies.orb = OrbStrategyConfig.model_validate(payload)
            elif sid == "vwap":
                payload = cfg.strategies.vwap.model_dump()
                payload.update(patch)
                cfg.strategies.vwap = VwapStrategyConfig.model_validate(payload)
            elif sid == "gap_orb":
                payload = cfg.strategies.gap_orb.model_dump()
                payload.update(patch)
                cfg.strategies.gap_orb = GapOrbStrategyConfig.model_validate(payload)
            elif sid == "vwap_mean_reversion":
                payload = cfg.strategies.vwap_mean_reversion.model_dump()
                payload.update(patch)
                cfg.strategies.vwap_mean_reversion = VwapMeanReversionStrategyConfig.model_validate(payload)

        if request.enabled_strategies is not None:
            cfg.strategies.orb.enabled = "orb" in enabled
            cfg.strategies.vwap.enabled = "vwap" in enabled
            cfg.strategies.gap_orb.enabled = "gap_orb" in enabled
            cfg.strategies.vwap_mean_reversion.enabled = "vwap_mean_reversion" in enabled
        return cfg

    def validate_request_overrides(self, request: BacktestRequest) -> None:
        self._resolve_config(request)

    def _build_engine(self, run_session_factory: sessionmaker, resolved_config: AppConfig) -> TradingEngine:
        return TradingEngine(
            run_session_factory,
            resolved_config,
            [
                NoOpStrategy(),
                OpeningRangeBreakoutStrategy(resolved_config.strategies.orb),
                GapFilteredOrbStrategy(resolved_config.strategies.gap_orb),
                VwapContinuationStrategy(resolved_config.strategies.vwap),
                VwapMeanReversionStrategy(resolved_config.strategies.vwap_mean_reversion),
            ],
            state_store=None,
        )

    def _config_snapshot(self, request: BacktestRequest, provider: str, dataset_keys: list[str], resolved_config: AppConfig) -> dict:
        req_dict = self._request_dict(request)
        return {
            "provider": provider,
            **req_dict,
            "start_date": request.start_date.isoformat(),
            "end_date": request.end_date.isoformat(),
            "interval": ensure_supported_interval(request.interval),
            "friction": resolved_config.friction.model_dump(),
            "risk": resolved_config.risk.model_dump(),
            "strategies": resolved_config.strategies.model_dump(),
            "enabled_strategies": [
                sid
                for sid, enabled in {
                    "orb": resolved_config.strategies.orb.enabled,
                    "vwap": resolved_config.strategies.vwap.enabled,
                    "gap_orb": resolved_config.strategies.gap_orb.enabled,
                    "vwap_mean_reversion": resolved_config.strategies.vwap_mean_reversion.enabled,
                }.items()
                if enabled
            ],
            "dataset_keys": dataset_keys,
        }

    def _set_job_progress(self, job_id: int, **patch) -> None:
        with self.main_session_factory() as session:
            row = session.get(BacktestJobRecord, job_id)
            if row is None:
                return
            progress = json.loads(row.progress_json or "{}")
            progress.update(patch)
            row.progress_json = json.dumps(progress)
            session.commit()

    def _job_cancelled(self, job_id: int) -> bool:
        with self.main_session_factory() as session:
            row = session.get(BacktestJobRecord, job_id)
            return bool(row and row.cancel_requested)

    def execute_request(self, provider: str, request: BacktestRequest, job_id: int | None = None) -> dict:
        normalized_interval = ensure_supported_interval(request.interval)
        symbols = sorted({s.strip().upper() for s in request.symbols if s.strip()})
        keys: list[str] = []
        all_bars: list = []
        for symbol in symbols:
            row = self.cache.find_dataset(provider=provider, symbol=symbol, interval=normalized_interval, start_date=request.start_date, end_date=request.end_date, adjusted=request.adjusted)
            if row is None:
                raise ValueError(f"no_cached_dataset_available:{symbol}")
            keys.append(row.cache_key)
            bars = self.cache.load_bars(row.cache_key)
            all_bars.extend(bars)
        all_bars.sort(key=lambda b: (b.ts, b.symbol))
        if job_id is not None:
            self._set_job_progress(job_id, total_bars_expected=len(all_bars), bars_processed=0, percent_complete=0.0, latest_event_message="dataset_loaded")

        csv_lines = ["timestamp,symbol,open,high,low,close,volume"]
        for bar in all_bars:
            csv_lines.append(f"{bar.ts.isoformat()},{bar.symbol},{bar.open},{bar.high},{bar.low},{bar.close},{bar.volume}")
        tmp_csv = self.cache.cache_dir / f"backtest_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}.csv"
        tmp_csv.write_text("\n".join(csv_lines), encoding="utf-8")

        started = datetime.now(timezone.utc)
        resolved_config = self._resolve_config(request)
        run_snapshot = self._config_snapshot(request, provider, keys, resolved_config)
        with self.main_session_factory() as session:
            run = BacktestRunRecord(
                provider=provider,
                symbols_csv=",".join(symbols),
                interval=normalized_interval,
                start_date=request.start_date.isoformat(),
                end_date=request.end_date.isoformat(),
                adjusted=int(request.adjusted),
                dataset_keys_csv=",".join(keys),
                status="RUNNING",
                config_json=json.dumps(run_snapshot),
                started_at=started,
            )
            session.add(run)
            session.commit()
            run_id = run.id

        with tempfile.TemporaryDirectory(prefix="tradehelm_bt_") as tmp_dir:
            run_db_path = Path(tmp_dir) / "run.db"
            run_session_factory = create_session_factory(f"sqlite:///{run_db_path}")
            engine = self._build_engine(run_session_factory, resolved_config)
            engine.startup()
            try:
                engine.load_replay(str(tmp_csv))
                engine.set_mode(BotMode.PAPER, reason="backtest_run")
                bars_processed = 0
                for bar in engine.market_data.bars():
                    if job_id is not None and self._job_cancelled(job_id):
                        raise RuntimeError("job_cancelled")
                    engine._roll_day_if_needed(bar.ts)
                    engine.risk.on_bar()
                    engine.broker.on_bar(bar)
                    engine._trade_bar(bar)
                    bars_processed += 1
                    if job_id is not None and (bars_processed % 10 == 0 or bars_processed == len(all_bars)):
                        pct = (bars_processed / max(len(all_bars), 1)) * 100.0
                        self._set_job_progress(
                            job_id,
                            bars_processed=bars_processed,
                            percent_complete=round(pct, 2),
                            current_symbol=bar.symbol,
                            current_timestamp=bar.ts.isoformat(),
                            latest_event_message=f"processed_bar_{bars_processed}",
                        )
                status = "COMPLETED"
            except RuntimeError as exc:
                if str(exc) == "job_cancelled":
                    status = "CANCELLED"
                else:
                    status = "FAILED"
                    raise
            except Exception:
                status = "FAILED"
                raise
            finally:
                engine.shutdown()

            run_analytics = AnalyticsService(run_session_factory)
            summary = run_analytics.summary()
            trades = run_analytics.trades()
            decisions = run_analytics.decisions(limit=50000)
            artifacts = self.run_analysis.build_run_artifacts(trades=trades, decisions=decisions, summary=summary)

        completed = datetime.now(timezone.utc)
        with self.main_session_factory() as session:
            row = session.get(BacktestRunRecord, run_id)
            assert row is not None
            row.status = status
            row.completed_at = completed
            row.summary_json = json.dumps({**summary, **artifacts["headline_summary"]})
            row.trades_json = json.dumps(trades)
            row.decisions_json = json.dumps(decisions)
            row.equity_curve_json = json.dumps(artifacts["equity_curve"])
            row.symbol_summary_json = json.dumps(artifacts["symbol_summary"])
            row.decision_summary_json = json.dumps(artifacts["decision_summary"])
            row.strategy_summary_json = json.dumps(artifacts["strategy_summary"])
            row.trade_timeline_json = json.dumps(artifacts["trade_timeline"])
            session.commit()
        return {
            "run_id": run_id,
            "status": status,
            "summary": {**summary, **artifacts["headline_summary"]},
            "config": run_snapshot,
            "equity_curve": artifacts["equity_curve"],
            "symbol_summary": artifacts["symbol_summary"],
            "decision_summary": artifacts["decision_summary"],
        }

    def run(self, provider: str, symbols: list[str], start_date: str, end_date: str, interval: str, adjusted: bool) -> dict:
        req = BacktestRequest(symbols=symbols, start_date=datetime.fromisoformat(start_date).date(), end_date=datetime.fromisoformat(end_date).date(), interval=interval, adjusted=adjusted)
        return self.execute_request(provider, req)

    def enqueue_job(self, provider: str, request: BacktestRequest) -> dict:
        now = datetime.now(timezone.utc)
        req = self._request_dict(request)
        with self.main_session_factory() as session:
            row = BacktestJobRecord(status="QUEUED", request_json=json.dumps(req), progress_json=json.dumps({"percent_complete": 0.0}), created_at=now)
            session.add(row)
            session.commit()
            job_id = row.id
        self.events.add(job_id, "job_queued", "Backtest job queued.")
        self._worker_wakeup.set()
        self._ensure_worker(provider)
        return self.get_job(job_id) or {"id": job_id, "status": "QUEUED"}

    def _ensure_worker(self, provider: str) -> None:
        with self._worker_lock:
            if self._worker_thread and self._worker_thread.is_alive():
                return
            self._worker_wakeup.clear()
            self._worker_thread = threading.Thread(target=self._worker_loop, args=(provider,), daemon=True)
            self._worker_thread.start()

    def _worker_loop(self, provider: str) -> None:
        idle_polls = 0
        while True:
            with self.main_session_factory() as session:
                row = session.scalars(select(BacktestJobRecord).where(BacktestJobRecord.status == "QUEUED").order_by(BacktestJobRecord.id)).first()
                if row is None:
                    idle_polls += 1
                    if idle_polls >= 5:
                        with self._worker_lock:
                            self._worker_thread = None
                        return
                    self._worker_wakeup.wait(timeout=0.1)
                    self._worker_wakeup.clear()
                    continue
                idle_polls = 0
                row.status = "RUNNING"
                row.started_at = datetime.now(timezone.utc)
                session.commit()
                job_id = row.id
                request = BacktestRequest.model_validate(json.loads(row.request_json or "{}"))
            self.events.add(job_id, "job_started", "Backtest job started.")
            try:
                result = self.execute_request(provider, request, job_id=job_id)
                status = result["status"]
                self.events.add(job_id, "run_completed" if status == "COMPLETED" else "run_cancelled", f"Backtest {status.lower()}.", run_id=result["run_id"])
                with self.main_session_factory() as session:
                    row = session.get(BacktestJobRecord, job_id)
                    assert row is not None
                    row.status = status
                    row.run_id = result["run_id"]
                    row.snapshot_json = json.dumps(result.get("config") or {})
                    row.completed_at = datetime.now(timezone.utc)
                    session.commit()
            except Exception as exc:
                self.events.add(job_id, "run_failed", "Backtest failed.", payload={"error": str(exc)})
                with self.main_session_factory() as session:
                    row = session.get(BacktestJobRecord, job_id)
                    assert row is not None
                    row.status = "FAILED"
                    row.error_message = str(exc)
                    row.completed_at = datetime.now(timezone.utc)
                    session.commit()

    def list_jobs(self) -> list[dict]:
        with self.main_session_factory() as session:
            rows = session.scalars(select(BacktestJobRecord).order_by(desc(BacktestJobRecord.id))).all()
            return [self._job_payload(row) for row in rows]

    def _job_payload(self, row: BacktestJobRecord) -> dict:
        return {
            "id": row.id,
            "status": row.status,
            "run_id": row.run_id,
            "request": json.loads(row.request_json or "{}"),
            "snapshot": json.loads(row.snapshot_json or "{}"),
            "progress": json.loads(row.progress_json or "{}"),
            "cancel_requested": bool(row.cancel_requested),
            "error_message": row.error_message,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }

    def get_job(self, job_id: int) -> dict | None:
        with self.main_session_factory() as session:
            row = session.get(BacktestJobRecord, job_id)
            return self._job_payload(row) if row else None

    def cancel_job(self, job_id: int) -> dict | None:
        with self.main_session_factory() as session:
            row = session.get(BacktestJobRecord, job_id)
            if row is None:
                return None
            if row.status in {"QUEUED", "RUNNING"}:
                row.cancel_requested = 1
                if row.status == "QUEUED":
                    row.status = "CANCELLED"
                    row.completed_at = datetime.now(timezone.utc)
                session.commit()
                self.events.add(job_id, "job_cancel_requested", "Cancellation requested.")
            return self._job_payload(row)

    def list_job_events(self, job_id: int) -> list[dict]:
        return self.events.list_for_job(job_id)

    def list_runs(self) -> list[dict]:
        with self.main_session_factory() as session:
            rows = session.scalars(select(BacktestRunRecord).order_by(desc(BacktestRunRecord.id))).all()
            return [
                {
                    "id": row.id,
                    "provider": row.provider,
                    "symbols": row.symbols_csv.split(",") if row.symbols_csv else [],
                    "interval": row.interval,
                    "start_date": row.start_date,
                    "end_date": row.end_date,
                    "adjusted": bool(row.adjusted),
                    "status": row.status,
                    "summary": json.loads(row.summary_json or "{}"),
                    "config": json.loads(row.config_json or "{}"),
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                }
                for row in rows
            ]

    def get_run(self, run_id: int) -> dict | None:
        with self.main_session_factory() as session:
            row = session.get(BacktestRunRecord, run_id)
            if row is None:
                return None
            return {
                "id": row.id,
                "provider": row.provider,
                "symbols": row.symbols_csv.split(",") if row.symbols_csv else [],
                "interval": row.interval,
                "start_date": row.start_date,
                "end_date": row.end_date,
                "adjusted": bool(row.adjusted),
                "status": row.status,
                "dataset_keys": row.dataset_keys_csv.split(",") if row.dataset_keys_csv else [],
                "summary": json.loads(row.summary_json or "{}"),
                "config": json.loads(row.config_json or "{}"),
                "trades": json.loads(row.trades_json or "[]"),
                "decisions": json.loads(row.decisions_json or "[]"),
                "equity_curve": json.loads(row.equity_curve_json or "[]"),
                "symbol_summary": json.loads(row.symbol_summary_json or "[]"),
                "strategy_summary": json.loads(row.strategy_summary_json or "[]"),
                "trade_timeline": json.loads(row.trade_timeline_json or "[]"),
                "decision_summary": json.loads(row.decision_summary_json or "{}"),
                "event_timeline": self.events.list_for_job(self._run_job_id(run_id)),
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            }

    def _run_job_id(self, run_id: int) -> int:
        with self.main_session_factory() as session:
            row = session.scalars(select(BacktestJobRecord).where(BacktestJobRecord.run_id == run_id).order_by(desc(BacktestJobRecord.id))).first()
            return row.id if row else -1

    def compare_runs(self, run_ids: list[int]) -> dict:
        unique_run_ids = []
        for run_id in run_ids:
            if run_id not in unique_run_ids:
                unique_run_ids.append(run_id)
        with self.main_session_factory() as session:
            rows = session.scalars(select(BacktestRunRecord).where(BacktestRunRecord.id.in_(unique_run_ids))).all()
        by_id = {row.id: row for row in rows}
        missing = [run_id for run_id in unique_run_ids if run_id not in by_id]
        comparisons: list[dict] = []
        for run_id in unique_run_ids:
            row = by_id.get(run_id)
            if row is None:
                continue
            summary = json.loads(row.summary_json or "{}")
            comparisons.append(
                {
                    "run_id": row.id,
                    "status": row.status,
                    "interval": row.interval,
                    "symbols": row.symbols_csv.split(",") if row.symbols_csv else [],
                    "adjusted": bool(row.adjusted),
                    "enabled_strategies": (json.loads(row.config_json or "{}").get("enabled_strategies") or []),
                    "net_realized_pnl": float(summary.get("net_realized_pnl", summary.get("net_pnl", 0.0)) or 0.0),
                    "gross_realized_pnl": float(summary.get("gross_realized_pnl", summary.get("gross_pnl", 0.0)) or 0.0),
                    "win_rate": float(summary.get("win_rate", 0.0) or 0.0),
                    "trade_count": int(summary.get("total_closed_trades", summary.get("trade_count", 0)) or 0),
                    "total_fees": float(summary.get("total_fees_paid", summary.get("total_fees", 0.0)) or 0.0),
                    "max_drawdown": float(summary.get("max_drawdown", 0.0) or 0.0),
                    "trades_per_day": float(summary.get("trades_per_day", 0.0) or 0.0),
                    "expectancy": float(summary.get("expectancy_per_trade", 0.0) or 0.0),
                    "symbol_summary": json.loads(row.symbol_summary_json or "[]"),
                    "strategy_summary": json.loads(row.strategy_summary_json or "[]"),
                }
            )
        return {"requested_run_ids": unique_run_ids, "missing_run_ids": missing, "runs": comparisons}
