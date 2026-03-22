"""Cached-data backtest runner."""
from __future__ import annotations

import json
from datetime import datetime, timezone
import tempfile
from pathlib import Path

from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

from tradehelm.analytics.service import AnalyticsService
from tradehelm.config.models import AppConfig
from tradehelm.historical.cache import HistoricalCache
from tradehelm.historical.run_analysis import RunAnalysisService
from tradehelm.persistence.db import BacktestRunRecord, create_session_factory
from tradehelm.strategies.noop import NoOpStrategy
from tradehelm.strategies.orb import OpeningRangeBreakoutStrategy
from tradehelm.strategies.vwap import VwapContinuationStrategy
from tradehelm.trading_engine.engine import TradingEngine
from tradehelm.trading_engine.types import BotMode


class BacktestRunner:
    def __init__(self, session_factory: sessionmaker, cache: HistoricalCache, app_config: AppConfig) -> None:
        self.main_session_factory = session_factory
        self.cache = cache
        self.app_config = app_config
        self.run_analysis = RunAnalysisService()

    def _build_engine(self, run_session_factory: sessionmaker) -> TradingEngine:
        config = self.app_config.model_copy(deep=True)
        strategies = [
            NoOpStrategy(),
            OpeningRangeBreakoutStrategy(config.strategies.orb),
            VwapContinuationStrategy(config.strategies.vwap),
        ]
        return TradingEngine(run_session_factory, config, strategies, state_store=None)

    def _config_snapshot(
        self,
        provider: str,
        symbols: list[str],
        start_date: str,
        end_date: str,
        interval: str,
        adjusted: bool,
        dataset_keys: list[str],
    ) -> dict:
        config = self.app_config.model_copy(deep=True)
        return {
            "provider": provider,
            "symbols": symbols,
            "interval": interval,
            "start_date": start_date,
            "end_date": end_date,
            "adjusted": adjusted,
            "friction": config.friction.model_dump(),
            "risk": config.risk.model_dump(),
            "strategies": config.strategies.model_dump(),
            "enabled_strategies": [
                "orb" if config.strategies.orb.enabled else None,
                "vwap" if config.strategies.vwap.enabled else None,
            ],
            "dataset_keys": dataset_keys,
        }

    def run(self, provider: str, symbols: list[str], start_date: str, end_date: str, interval: str, adjusted: bool) -> dict:
        keys: list[str] = []
        csv_lines = ["timestamp,symbol,open,high,low,close,volume"]
        for symbol in symbols:
            row = self.cache.find_dataset(
                provider=provider,
                symbol=symbol,
                interval=interval,
                start_date=datetime.fromisoformat(start_date).date(),
                end_date=datetime.fromisoformat(end_date).date(),
                adjusted=adjusted,
            )
            if row is None:
                raise ValueError(f"no_cached_dataset_available:{symbol}")
            keys.append(row.cache_key)
            bars = self.cache.load_bars(row.cache_key)
            for bar in bars:
                csv_lines.append(f"{bar.ts.isoformat()},{bar.symbol},{bar.open},{bar.high},{bar.low},{bar.close},{bar.volume}")

        csv_lines = [csv_lines[0], *sorted(csv_lines[1:])]
        tmp_csv = self.cache.cache_dir / f"backtest_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}.csv"
        tmp_csv.write_text("\n".join(csv_lines), encoding="utf-8")

        started = datetime.now(timezone.utc)
        run_snapshot = self._config_snapshot(provider, symbols, start_date, end_date, interval, adjusted, keys)
        run_snapshot["enabled_strategies"] = [s for s in run_snapshot["enabled_strategies"] if s is not None]
        with self.main_session_factory() as session:
            run = BacktestRunRecord(
                provider=provider,
                symbols_csv=",".join(symbols),
                interval=interval,
                start_date=start_date,
                end_date=end_date,
                adjusted=int(adjusted),
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
            run_db_url = f"sqlite:///{run_db_path}"
            run_session_factory = create_session_factory(run_db_url)
            engine = self._build_engine(run_session_factory)
            engine.startup()
            try:
                engine.load_replay(str(tmp_csv))
                engine.set_mode(BotMode.PAPER, reason="backtest_run")
                engine._run_replay_loop()  # deterministic synchronous replay for backtests
                status = "COMPLETED"
            except Exception:
                status = "FAILED"
                raise
            finally:
                engine.shutdown()

            run_analytics = AnalyticsService(run_session_factory)
            summary = run_analytics.summary()
            trades = run_analytics.trades()
            decisions = run_analytics.decisions(limit=50000)
            artifacts = self.run_analysis.build_run_artifacts(trades=trades, decisions=decisions)

        completed = datetime.now(timezone.utc)

        with self.main_session_factory() as session:
            row = session.get(BacktestRunRecord, run_id)
            assert row is not None
            row.status = status
            row.completed_at = completed
            row.summary_json = json.dumps(summary)
            row.trades_json = json.dumps(trades)
            row.decisions_json = json.dumps(decisions)
            row.equity_curve_json = json.dumps(artifacts["equity_curve"])
            row.symbol_summary_json = json.dumps(artifacts["symbol_summary"])
            row.decision_summary_json = json.dumps(artifacts["decision_summary"])
            session.commit()

        return {
            "run_id": run_id,
            "status": status,
            "summary": summary,
            "dataset_keys": keys,
            "config": run_snapshot,
            "equity_curve": artifacts["equity_curve"],
            "symbol_summary": artifacts["symbol_summary"],
            "decision_summary": artifacts["decision_summary"],
        }

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
                    "dataset_keys": row.dataset_keys_csv.split(",") if row.dataset_keys_csv else [],
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
                "decision_summary": json.loads(row.decision_summary_json or "{}"),
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "completed_at": row.completed_at.isoformat() if row.completed_at else None,
            }

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
                    "net_realized_pnl": float(summary.get("net_realized_pnl", 0.0) or 0.0),
                    "gross_realized_pnl": float(summary.get("gross_realized_pnl", 0.0) or 0.0),
                    "win_rate": float(summary.get("win_rate", 0.0) or 0.0),
                    "trade_count": int(summary.get("total_closed_trades", 0) or 0),
                    "total_fees": float(summary.get("total_fees_paid", 0.0) or 0.0),
                    "best_trade": float(summary.get("best_trade", 0.0) or 0.0),
                    "worst_trade": float(summary.get("worst_trade", 0.0) or 0.0),
                    "symbol_summary": json.loads(row.symbol_summary_json or "[]"),
                }
            )
        return {
            "requested_run_ids": unique_run_ids,
            "missing_run_ids": missing,
            "runs": comparisons,
        }
