"""Main trading engine orchestration."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

from tradehelm.config.models import AppConfig
from tradehelm.persistence.db import EventLog, FillRecord, OrderRecord, PositionRecord, ReplaySessionRecord, StateTransition
from tradehelm.providers.interfaces import Strategy
from tradehelm.providers.replay import ReplayMarketDataProvider
from tradehelm.risk.engine import RiskEngine
from tradehelm.trading_engine.cost_model import GenericCostModel
from tradehelm.trading_engine.paper_broker import PaperBroker
from tradehelm.trading_engine.state_machine import BotStateMachine
from tradehelm.trading_engine.types import BotMode, OrderType


@dataclass
class StrategyState:
    strategy: Strategy
    enabled: bool = True


class TradingEngine:
    """Coordinates mode, replay, strategy, risk and paper execution."""

    def __init__(self, session_factory: sessionmaker, config: AppConfig, strategies: list[Strategy]) -> None:
        self.session_factory = session_factory
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

        self.replay_loaded = False
        self.replay_path: str | None = None
        self.replay_running = False
        self.replay_stop_requested = False
        self.replay_started_at: datetime | None = None
        self.replay_completed_at: datetime | None = None
        self._replay_thread: threading.Thread | None = None
        self._lock = threading.Lock()

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

    def apply_config(self, new_config: AppConfig) -> dict[str, Any]:
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
        return {
            "updated": True,
            "note": "Replay speed and risk/cost settings apply immediately to new bars and new orders.",
        }

    def set_mode(self, mode: BotMode, reason: str = "") -> BotMode:
        new_mode = self.state_machine.set_mode(mode)
        with self.session_factory() as s:
            s.add(StateTransition(mode=new_mode.value, reason=reason))
            s.commit()
        self.log("INFO", "mode", f"mode={new_mode.value}")
        if new_mode == BotMode.KILL_SWITCH:
            self.replay_stop_requested = True
            self.kill_switch_flatten()
        if new_mode == BotMode.STOPPED:
            self.replay_stop_requested = True
        return new_mode

    def load_replay(self, csv_path: str) -> None:
        self.market_data.load(csv_path)
        self.replay_loaded = True
        self.replay_path = csv_path
        self._reset_day_counters(None)
        with self.session_factory() as s:
            s.add(ReplaySessionRecord(dataset=csv_path, status="LOADED"))
            s.commit()

    def start_replay(self) -> dict[str, Any]:
        """Start replay worker in background thread."""
        with self._lock:
            if not self.replay_loaded:
                raise ValueError("replay not loaded")
            if self.replay_running:
                return {"started": False, "reason": "already running"}
            self.replay_stop_requested = False
            self.replay_running = True
            self.replay_started_at = datetime.now(timezone.utc)
            self.replay_completed_at = None
            self._replay_thread = threading.Thread(target=self._run_replay_worker, daemon=True)
            self._replay_thread.start()
            return {"started": True}

    def stop_replay(self) -> dict[str, Any]:
        """Request replay worker to stop."""
        self.replay_stop_requested = True
        return {"stop_requested": True}

    def _run_replay_worker(self) -> None:
        try:
            self._run_replay_loop()
        finally:
            self.replay_running = False
            self.replay_completed_at = datetime.now(timezone.utc)

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

    def _observe_bar(self, bar) -> None:
        for ss in self.strategies.values():
            if ss.enabled:
                intents = ss.strategy.on_bar(bar)
                if intents:
                    self.log("INFO", "observe_signal", f"{ss.strategy.strategy_id} intents={len(intents)}")

    def _trade_bar(self, bar) -> None:
        day_unrealized_pnl = self.unrealized_pnl()
        daily_pnl = self.day_realized_pnl + day_unrealized_pnl
        projected_open_symbols = {p["symbol"] for p in self.positions() if p["qty"] != 0}

        for ss in self.strategies.values():
            if not ss.enabled:
                continue
            for intent in ss.strategy.on_bar(bar):
                symbol = intent["symbol"]
                qty = int(intent["qty"])
                est_cost = self.cost_model.estimate_round_trip_cost(bar.close, qty)
                gross_edge = float(bar.close * qty * 0.002)
                net_edge = gross_edge - est_cost
                opening_new_symbol = symbol not in projected_open_symbols
                ok, reason = self.risk.validate(symbol, qty, bar.close, net_edge, daily_pnl, len(projected_open_symbols))
                if not ok:
                    self.log("WARN", "risk_reject", reason)
                    continue
                self.broker.submit_order(
                    symbol=symbol,
                    side=intent["side"],
                    qty=qty,
                    order_type=OrderType.MARKET,
                )
                self.risk.trades_today += 1
                if opening_new_symbol:
                    projected_open_symbols.add(symbol)

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
                }
                for p in rows
            ]

    def trades(self) -> list[dict]:
        with self.session_factory() as s:
            from tradehelm.persistence.db import ClosedTradeRecord
            return [t.__dict__ for t in s.scalars(select(ClosedTradeRecord)).all()]

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
