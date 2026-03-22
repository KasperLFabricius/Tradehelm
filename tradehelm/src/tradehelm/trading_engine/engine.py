"""Main trading engine orchestration."""
from __future__ import annotations

from dataclasses import dataclass

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
        self.broker = PaperBroker(session_factory, self.cost_model)
        self.strategies = {s.strategy_id: StrategyState(strategy=s, enabled=True) for s in strategies}
        self.replay_loaded = False
        self.replay_path: str | None = None

    def log(self, level: str, event_type: str, message: str) -> None:
        with self.session_factory() as s:
            s.add(EventLog(level=level, event_type=event_type, message=message))
            s.commit()

    def set_mode(self, mode: BotMode, reason: str = "") -> BotMode:
        new_mode = self.state_machine.set_mode(mode)
        with self.session_factory() as s:
            s.add(StateTransition(mode=new_mode.value, reason=reason))
            s.commit()
        self.log("INFO", "mode", f"mode={new_mode.value}")
        if new_mode == BotMode.KILL_SWITCH:
            self.kill_switch_flatten()
        return new_mode

    def load_replay(self, csv_path: str) -> None:
        self.market_data.load(csv_path)
        self.replay_loaded = True
        self.replay_path = csv_path
        with self.session_factory() as s:
            s.add(ReplaySessionRecord(dataset=csv_path, status="LOADED"))
            s.commit()

    def run_replay(self) -> None:
        if not self.replay_loaded:
            raise ValueError("replay not loaded")
        for bar in self.market_data.bars():
            self.risk.on_bar()
            self.broker.on_bar(bar)
            mode = self.state_machine.mode
            if mode in {BotMode.STOPPED, BotMode.KILL_SWITCH}:
                break
            if mode == BotMode.OBSERVE:
                self._observe_bar(bar)
            elif mode == BotMode.PAPER:
                self._trade_bar(bar)
            elif mode == BotMode.HALTED:
                continue

    def _observe_bar(self, bar) -> None:
        for ss in self.strategies.values():
            if ss.enabled:
                intents = ss.strategy.on_bar(bar)
                if intents:
                    self.log("INFO", "observe_signal", f"{ss.strategy.strategy_id} intents={len(intents)}")

    def _trade_bar(self, bar) -> None:
        daily_pnl = self.realized_pnl() + self.unrealized_pnl()
        current_positions = len([p for p in self.positions() if p["qty"] != 0])
        for ss in self.strategies.values():
            if not ss.enabled:
                continue
            for intent in ss.strategy.on_bar(bar):
                qty = int(intent["qty"])
                est_cost = self.cost_model.estimate_round_trip_cost(bar.close, qty)
                gross_edge = float(bar.close * qty * 0.002)
                net_edge = gross_edge - est_cost
                ok, reason = self.risk.validate(bar.symbol, qty, bar.close, net_edge, daily_pnl, current_positions)
                if not ok:
                    self.log("WARN", "risk_reject", reason)
                    continue
                self.broker.submit_order(
                    symbol=intent["symbol"],
                    side=intent["side"],
                    qty=qty,
                    order_type=OrderType.MARKET,
                )
                self.risk.trades_today += 1

    def kill_switch_flatten(self) -> None:
        with self.session_factory() as s:
            for order in s.scalars(select(OrderRecord).where(OrderRecord.status.in_(["NEW", "PARTIALLY_FILLED"]))).all():
                order.status = "CANCELLED"
            for p in s.scalars(select(PositionRecord)).all():
                p.qty = 0
            s.commit()

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

    def state(self) -> dict:
        return {
            "mode": self.state_machine.mode.value,
            "replay_loaded": self.replay_loaded,
            "replay_path": self.replay_path,
            "active_strategy_count": len([s for s in self.strategies.values() if s.enabled]),
            "open_positions": len([p for p in self.positions() if p["qty"] != 0]),
            "working_orders": len([o for o in self.orders() if o["status"] in {"NEW", "PARTIALLY_FILLED"}]),
            "realized_pnl": self.realized_pnl(),
            "unrealized_pnl": self.unrealized_pnl(),
            "daily_loss_limit": self.config.risk.max_daily_loss,
        }
