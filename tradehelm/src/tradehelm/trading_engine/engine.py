"""Main trading engine orchestration."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, desc, select
from sqlalchemy.orm import sessionmaker

from tradehelm.analytics.service import AnalyticsService
from tradehelm.config.models import AppConfig
from tradehelm.persistence.db import (
    ClosedTradeRecord,
    DecisionRecord,
    EventLog,
    FillRecord,
    OrderRecord,
    PositionRecord,
    ReplaySessionRecord,
    StateTransition,
)
from tradehelm.persistence.state_store import PersistedStateStore, RuntimeMetadata
from tradehelm.providers.interfaces import Strategy
from tradehelm.providers.replay import ReplayMarketDataProvider
from tradehelm.risk.engine import RiskEngine
from tradehelm.trading_engine.cost_model import GenericCostModel
from tradehelm.trading_engine.errors import InvalidReplayPathError, InvalidTransitionError, ReplayNotLoadedError, StrategyNotFoundError
from tradehelm.trading_engine.paper_broker import PaperBroker
from tradehelm.trading_engine.state_machine import BotStateMachine
from tradehelm.trading_engine.types import Bar, BotMode, OrderType, StrategyAction, StrategyIntent


@dataclass
class StrategyState:
    strategy: Strategy
    enabled: bool = True


class TradingEngine:
    """Coordinates mode, replay, strategy, risk and paper execution."""

    def __init__(
        self,
        session_factory: sessionmaker,
        config: AppConfig,
        strategies: list[Strategy],
        state_store: PersistedStateStore | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.state_store = state_store
        self.config = config
        self.state_machine = BotStateMachine()
        self.market_data = ReplayMarketDataProvider(replay_speed=config.replay_speed)
        self.cost_model = GenericCostModel(config.friction)
        self.risk = RiskEngine(config.risk)
        self.day_realized_pnl = 0.0
        self.current_trading_day: date | None = None
        self.broker = PaperBroker(
            session_factory,
            self.cost_model,
            on_position_closed=self.risk.on_exit,
            on_realized_pnl=self._on_realized_delta,
        )
        self.strategies = {s.strategy_id: StrategyState(strategy=s, enabled=True) for s in strategies}
        self.analytics = AnalyticsService(session_factory)

        self.replay_loaded = False
        self.replay_path: str | None = None
        self.replay_running = False
        self.replay_stop_requested = False
        self.replay_started_at: datetime | None = None
        self.replay_completed_at: datetime | None = None
        self.active_replay_session_id: int | None = None
        self._replay_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def startup(self) -> None:
        if self.state_store is None:
            return
        persisted_config = self.state_store.load_or_init_config(self.config)
        self.apply_config(persisted_config, persist=False)

        metadata = RuntimeMetadata.model_validate(self.state_store.load_metadata() or {})
        if metadata.replay_speed is not None:
            self.config.replay_speed = metadata.replay_speed
            self.market_data.replay_speed = metadata.replay_speed
        if metadata.replay_path:
            replay_path = metadata.resolved_replay_path()
            if replay_path and Path(replay_path).exists():
                try:
                    self.load_replay(replay_path)
                except InvalidReplayPathError:
                    self.log("WARN", "startup", f"Failed to restore replay path: {replay_path}")
        self.replay_running = False
        self.replay_stop_requested = False
        self.set_mode(BotMode.STOPPED, reason="startup_safe_default")

    def shutdown(self) -> None:
        if self.replay_running:
            self.replay_stop_requested = True
            if self._replay_thread is not None:
                self._replay_thread.join(timeout=2)
        self.replay_running = False
        self.replay_stop_requested = True
        self.set_mode(BotMode.STOPPED, reason="shutdown")

    def _persist_runtime_metadata(self) -> None:
        if self.state_store is None:
            return
        metadata = RuntimeMetadata.from_engine_state(
            replay_path=self.replay_path,
            replay_speed=self.config.replay_speed,
            last_mode=self.state_machine.mode,
        )
        self.state_store.save_metadata(metadata.model_dump())

    def _on_realized_delta(self, delta: float) -> None:
        self.day_realized_pnl += delta

    def _reset_day_counters(self, trading_day: date | None) -> None:
        self.current_trading_day = trading_day
        self.day_realized_pnl = 0.0
        self.risk.trades_today = 0

    def _roll_day_if_needed(self, bar_ts: datetime) -> None:
        bar_day = bar_ts.date()
        if self.current_trading_day != bar_day:
            self._reset_day_counters(bar_day)

    def log(self, level: str, event_type: str, message: str) -> None:
        with self.session_factory() as s:
            s.add(EventLog(level=level, event_type=event_type, message=message))
            s.commit()

    def _apply_strategy_configs(self) -> None:
        for sid, state in self.strategies.items():
            if hasattr(state.strategy, "config"):
                if sid == "orb":
                    state.strategy.config = self.config.strategies.orb
                elif sid == "vwap":
                    state.strategy.config = self.config.strategies.vwap

    def apply_config(self, new_config: AppConfig, persist: bool = True) -> dict[str, Any]:
        """Apply runtime config updates to active engine components."""
        self.config = new_config
        self.cost_model = GenericCostModel(new_config.friction)
        previous_risk = self.risk
        self.risk = RiskEngine(new_config.risk)
        self.risk.trades_today = previous_risk.trades_today
        self.risk.cooldown_left = previous_risk.cooldown_left
        self.market_data.replay_speed = new_config.replay_speed
        self.broker.cost_model = self.cost_model
        self.broker.on_position_closed = self.risk.on_exit
        self._apply_strategy_configs()
        if persist and self.state_store is not None:
            self.state_store.save_config(new_config)
            self._persist_runtime_metadata()
        return {
            "updated": True,
            "note": "Replay speed and risk/cost settings apply immediately to new bars and new orders.",
        }

    def set_mode(self, mode: BotMode, reason: str = "") -> BotMode:
        try:
            new_mode = self.state_machine.set_mode(mode)
        except ValueError as exc:
            raise InvalidTransitionError(str(exc)) from exc
        with self.session_factory() as s:
            s.add(StateTransition(mode=new_mode.value, reason=reason))
            s.commit()
        self.log("INFO", "mode", f"mode={new_mode.value}")
        if new_mode == BotMode.KILL_SWITCH:
            self.replay_stop_requested = True
            self.kill_switch_flatten()
        if new_mode == BotMode.STOPPED:
            self.replay_stop_requested = True
        self._persist_runtime_metadata()
        return new_mode

    def _update_replay_session(self, status: str, started_at: datetime | None = None, completed_at: datetime | None = None) -> None:
        if self.active_replay_session_id is None:
            return
        with self.session_factory() as s:
            session = s.get(ReplaySessionRecord, self.active_replay_session_id)
            if session is None:
                return
            session.status = status
            if started_at is not None:
                session.started_at = started_at
            if completed_at is not None:
                session.completed_at = completed_at
            s.commit()

    def load_replay(self, csv_path: str) -> None:
        path = Path(csv_path).expanduser().resolve()
        if not path.exists() or not path.is_file() or path.suffix.lower() != ".csv":
            raise InvalidReplayPathError(f"Replay file is invalid or missing: {csv_path}")
        try:
            self.market_data.load(str(path))
        except Exception as exc:
            raise InvalidReplayPathError(f"Unable to read replay CSV: {csv_path}") from exc
        self.replay_loaded = True
        self.replay_path = str(path)
        self._reset_day_counters(None)
        self._persist_runtime_metadata()
        now = datetime.utcnow()
        with self.session_factory() as s:
            row = ReplaySessionRecord(dataset=str(path), loaded_at=now, status="LOADED")
            s.add(row)
            s.commit()
            self.active_replay_session_id = row.id

    def start_replay(self) -> dict[str, Any]:
        """Start replay worker in background thread."""
        with self._lock:
            if not self.replay_loaded:
                raise ReplayNotLoadedError("Replay dataset must be loaded before start.")
            if self.replay_running:
                return {"started": False, "reason": "already running"}
            self.replay_stop_requested = False
            self.replay_running = True
            self.replay_started_at = datetime.now(timezone.utc)
            self.replay_completed_at = None
            self._update_replay_session("RUNNING", started_at=datetime.utcnow())
            self._replay_thread = threading.Thread(target=self._run_replay_worker, daemon=True)
            self._replay_thread.start()
            return {"started": True}

    def stop_replay(self) -> dict[str, Any]:
        """Request replay worker to stop."""
        self.replay_stop_requested = True
        return {"stop_requested": True}

    def set_strategy_enabled(self, strategy_id: str, enabled: bool) -> dict[str, Any]:
        strategy = self.strategies.get(strategy_id)
        if strategy is None:
            raise StrategyNotFoundError(f"Strategy not found: {strategy_id}")
        strategy.enabled = enabled
        self.log("INFO", "strategy", f"strategy={strategy_id} enabled={enabled}")
        return {"strategy_id": strategy_id, "enabled": enabled}

    def _run_replay_worker(self) -> None:
        status = "COMPLETED"
        try:
            self._run_replay_loop()
            if self.replay_stop_requested:
                status = "STOPPED"
        except Exception:
            status = "FAILED"
            raise
        finally:
            self.replay_running = False
            self.replay_completed_at = datetime.now(timezone.utc)
            self._update_replay_session(status, completed_at=datetime.utcnow())

    def _run_replay_loop(self) -> None:
        for bar in self.market_data.bars():
            if self.replay_stop_requested:
                break
            mode = self.state_machine.mode
            if mode in {BotMode.STOPPED, BotMode.KILL_SWITCH}:
                break

            self._roll_day_if_needed(bar.ts)
            self.risk.on_bar()
            self.broker.on_bar(bar)

            if mode == BotMode.OBSERVE:
                self._observe_bar(bar)
            elif mode == BotMode.PAPER:
                self._trade_bar(bar)

            pace = max(0.0, 1.0 / max(self.market_data.replay_speed, 0.1))
            time.sleep(pace)

    def _observe_bar(self, bar: Bar) -> None:
        for ss in self.strategies.values():
            if ss.enabled:
                intents = ss.strategy.on_bar(bar)
                if intents:
                    self.log("INFO", "observe_signal", f"{ss.strategy.strategy_id} intents={len(intents)}")

    def _record_decision(self, *args, **kwargs) -> None:
        """Persist strategy decision (typed or legacy signature)."""
        accepted = bool(kwargs.get("accepted", False))
        reason = str(kwargs.get("reason", ""))

        strategy_id: str
        symbol: str
        side: str
        qty: int
        action: str = str(kwargs.get("action", "UNKNOWN"))

        if args and isinstance(args[0], StrategyIntent):
            intent = args[0]
            strategy_id = intent.strategy_id
            symbol = intent.symbol
            side = intent.side.value
            qty = intent.qty
            action = intent.action.value
            if len(args) >= 3:
                accepted = bool(args[1])
                reason = str(args[2])
        elif len(args) >= 4 and isinstance(args[0], str):
            strategy_id = str(args[0])
            symbol = str(args[1])
            side = str(args[2])
            qty = int(args[3])
            if len(args) >= 5:
                accepted = bool(args[4])
            if len(args) >= 6:
                reason = str(args[5])
        else:
            raise ValueError("Invalid _record_decision signature")

        with self.session_factory() as s:
            s.add(
                DecisionRecord(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    side=side,
                    qty=qty,
                    action=action,
                    accepted=1 if accepted else 0,
                    reason=reason,
                    mode=self.state_machine.mode.value,
                )
            )
            s.commit()


    def _open_qty(self, symbol: str) -> int:
        with self.session_factory() as s:
            row = s.get(PositionRecord, symbol)
            return row.qty if row is not None else 0

    def _normalize_intent(self, fallback_strategy_id: str, intent: StrategyIntent | dict) -> StrategyIntent:
        if isinstance(intent, StrategyIntent):
            return intent
        # backward-compatible fallback for older dict based tests/callers
        return StrategyIntent(
            symbol=str(intent["symbol"]),
            side=intent["side"],
            qty=int(intent["qty"]),
            action=intent.get("action", StrategyAction.ENTRY),
            strategy_id=intent.get("strategy_id", fallback_strategy_id),
            reason=str(intent.get("reason", "legacy_intent")),
            metadata=dict(intent.get("metadata", {})),
        )

    def _notify_strategy(self, strategy: Strategy, hook: str, *args) -> None:
        fn = getattr(strategy, hook, None)
        if callable(fn):
            fn(*args)

    def _trade_bar(self, bar: Bar) -> None:
        day_unrealized_pnl = self.unrealized_pnl()
        daily_pnl = self.day_realized_pnl + day_unrealized_pnl
        projected_open_symbols = {p["symbol"] for p in self.positions() if p["qty"] != 0}
        submitted_signatures: set[tuple[str, str, str, str, int]] = set()

        for ss in self.strategies.values():
            if not ss.enabled:
                continue
            raw_intents = ss.strategy.on_bar(bar)
            for raw_intent in raw_intents:
                intent = self._normalize_intent(ss.strategy.strategy_id, raw_intent)
                sig = (intent.strategy_id, intent.symbol, intent.action.value, intent.side.value, intent.qty)
                if sig in submitted_signatures:
                    self._record_decision(intent, accepted=False, reason="duplicate_intent_suppressed")
                    if intent.action == StrategyAction.ENTRY:
                        self._notify_strategy(ss.strategy, "on_entry_rejected", intent, bar, "duplicate_intent_suppressed")
                    else:
                        self._notify_strategy(ss.strategy, "on_exit_rejected", intent, bar, "duplicate_intent_suppressed")
                    continue
                submitted_signatures.add(sig)

                if intent.action == StrategyAction.ENTRY and self._open_qty(intent.symbol) != 0:
                    self._record_decision(intent, accepted=False, reason="entry_suppressed_existing_position")
                    self._notify_strategy(ss.strategy, "on_entry_rejected", intent, bar, "entry_suppressed_existing_position")
                    continue

                if intent.action == StrategyAction.EXIT:
                    if self._open_qty(intent.symbol) == 0:
                        self._record_decision(intent, accepted=False, reason="exit_rejected_no_position")
                        self._notify_strategy(ss.strategy, "on_exit_rejected", intent, bar, "exit_rejected_no_position")
                        continue
                    order_id = self.broker.submit_order(
                        symbol=intent.symbol,
                        side=intent.side,
                        qty=intent.qty,
                        order_type=OrderType.MARKET,
                    )
                    self._record_decision(intent, accepted=True, reason=intent.reason or f"exit_accepted order={order_id}")
                    self._notify_strategy(ss.strategy, "on_exit_accepted", intent, bar)
                    continue

                est_cost = self.cost_model.estimate_round_trip_cost(bar.close, intent.qty)
                gross_edge = float(bar.close * intent.qty * 0.03)
                net_edge = gross_edge - est_cost
                opening_new_symbol = intent.symbol not in projected_open_symbols
                ok, reason = self.risk.validate(intent.symbol, intent.qty, bar.close, net_edge, daily_pnl, len(projected_open_symbols))
                if not ok:
                    self._record_decision(intent, accepted=False, reason=reason)
                    self._notify_strategy(ss.strategy, "on_entry_rejected", intent, bar, reason)
                    self.log("WARN", "risk_reject", reason)
                    continue

                order_id = self.broker.submit_order(
                    symbol=intent.symbol,
                    side=intent.side,
                    qty=intent.qty,
                    order_type=OrderType.MARKET,
                )
                accept_reason = intent.reason or f"entry_accepted order={order_id}"
                self._record_decision(intent, accepted=True, reason=accept_reason)
                self._notify_strategy(ss.strategy, "on_entry_accepted", intent, bar)
                self.risk.trades_today += 1
                if opening_new_symbol:
                    projected_open_symbols.add(intent.symbol)

    def kill_switch_flatten(self) -> None:
        now = datetime.now(timezone.utc)
        with self.session_factory() as s:
            for order in s.scalars(select(OrderRecord).where(OrderRecord.status.in_(["NEW", "PARTIALLY_FILLED"]))).all():
                order.status = "CANCELLED"
            open_symbols = [p.symbol for p in s.scalars(select(PositionRecord).where(PositionRecord.qty != 0)).all()]
            latest_prices = {sym: self.broker.last_prices.get(sym) for sym in open_symbols}
            s.commit()

        for symbol in open_symbols:
            self.broker.force_flatten_symbol(symbol, now, latest_prices.get(symbol))

    def reset_paper_records(self) -> dict[str, int]:
        with self.session_factory() as s:
            counts = {
                "orders": s.query(OrderRecord).count(),
                "fills": s.query(FillRecord).count(),
                "positions": s.query(PositionRecord).count(),
                "closed_trades": s.query(ClosedTradeRecord).count(),
                "decisions": s.query(DecisionRecord).count(),
                "replay_sessions": s.query(ReplaySessionRecord).count(),
            }
            s.execute(delete(OrderRecord))
            s.execute(delete(FillRecord))
            s.execute(delete(PositionRecord))
            s.execute(delete(ClosedTradeRecord))
            s.execute(delete(DecisionRecord))
            s.execute(delete(ReplaySessionRecord))
            s.commit()
        self._reset_day_counters(None)
        self.replay_started_at = None
        self.replay_completed_at = None
        self.active_replay_session_id = None
        return counts

    def orders(self) -> list[dict]:
        with self.session_factory() as s:
            return [o.__dict__ for o in s.scalars(select(OrderRecord).order_by(desc(OrderRecord.ts))).all()]

    def fills(self) -> list[dict]:
        with self.session_factory() as s:
            return [f.__dict__ for f in s.scalars(select(FillRecord).order_by(desc(FillRecord.ts))).all()]

    def positions(self) -> list[dict]:
        with self.session_factory() as s:
            rows = s.scalars(select(PositionRecord)).all()
            return [
                {
                    "symbol": p.symbol,
                    "qty": p.qty,
                    "avg_entry": p.avg_entry,
                    "last_price": p.last_price,
                    "realized_pnl": p.realized_pnl,
                    "opened_at": p.opened_at.isoformat() if p.opened_at else None,
                    "cumulative_fees": p.cumulative_fees,
                }
                for p in rows
            ]

    def trades(self) -> list[dict]:
        return self.analytics.trades()

    def sessions(self) -> list[dict]:
        return self.analytics.sessions()

    def decisions(self) -> list[dict]:
        return self.analytics.decisions()

    def logs(self) -> list[dict]:
        with self.session_factory() as s:
            return [l.__dict__ for l in s.scalars(select(EventLog).order_by(desc(EventLog.ts))).all()]

    def realized_pnl(self) -> float:
        return sum(p["realized_pnl"] for p in self.positions())

    def unrealized_pnl(self) -> float:
        total = 0.0
        for p in self.positions():
            if p["qty"] == 0:
                continue
            total += (p["last_price"] - p["avg_entry"]) * p["qty"]
        return total

    def _dt(self, value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    def state(self) -> dict:
        return {
            "mode": self.state_machine.mode.value,
            "replay_loaded": self.replay_loaded,
            "replay_running": self.replay_running,
            "replay_stop_requested": self.replay_stop_requested,
            "replay_path": self.replay_path,
            "replay_started_at": self._dt(self.replay_started_at),
            "replay_completed_at": self._dt(self.replay_completed_at),
            "active_strategy_count": len([s for s in self.strategies.values() if s.enabled]),
            "open_positions": len([p for p in self.positions() if p["qty"] != 0]),
            "working_orders": len([o for o in self.orders() if o["status"] in {"NEW", "PARTIALLY_FILLED"}]),
            "realized_pnl": self.realized_pnl(),
            "day_realized_pnl": self.day_realized_pnl,
            "unrealized_pnl": self.unrealized_pnl(),
            "daily_loss_limit": self.config.risk.max_daily_loss,
            "current_trading_day": self.current_trading_day.isoformat() if self.current_trading_day else None,
        }
