"""Paper broker with simple fill simulation and friction."""
from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tradehelm.persistence.db import ClosedTradeRecord, FillRecord, OrderRecord, PositionRecord
from tradehelm.providers.interfaces import BrokerProvider
from tradehelm.trading_engine.cost_model import GenericCostModel
from tradehelm.trading_engine.types import Bar, OrderSide, OrderStatus, OrderType


@dataclass
class PositionState:
    qty: int = 0
    avg_entry: float = 0.0


class PaperBroker(BrokerProvider):
    """Simulation broker for market/limit order handling."""

    def __init__(self, session_factory: sessionmaker, cost_model: GenericCostModel) -> None:
        self.session_factory = session_factory
        self.cost_model = cost_model
        self.last_prices: dict[str, float] = {}

    def submit_order(self, symbol: str, side: OrderSide, qty: int, order_type: OrderType, limit_price: float | None = None) -> str:
        order_id = str(uuid4())
        with self.session_factory() as s:
            s.add(
                OrderRecord(
                    id=order_id,
                    symbol=symbol,
                    side=side.value,
                    qty=qty,
                    order_type=order_type.value,
                    limit_price=limit_price,
                    status=OrderStatus.NEW.value,
                )
            )
            s.commit()
        return order_id

    def cancel_order(self, order_id: str) -> None:
        with self.session_factory() as s:
            order = s.get(OrderRecord, order_id)
            if order and order.status in {OrderStatus.NEW.value, OrderStatus.PARTIALLY_FILLED.value}:
                order.status = OrderStatus.CANCELLED.value
                s.commit()

    def _fillable(self, order: OrderRecord, px: float) -> bool:
        if order.order_type == OrderType.MARKET.value:
            return True
        if order.side == OrderSide.BUY.value:
            return order.limit_price is not None and px <= order.limit_price
        return order.limit_price is not None and px >= order.limit_price

    def on_bar(self, bar: Bar) -> None:
        self.last_prices[bar.symbol] = bar.close
        with self.session_factory() as s:
            orders = s.scalars(
                select(OrderRecord).where(
                    OrderRecord.symbol == bar.symbol,
                    OrderRecord.status.in_([OrderStatus.NEW.value, OrderStatus.PARTIALLY_FILLED.value]),
                )
            ).all()
            for order in orders:
                if not self._fillable(order, bar.close):
                    continue
                remaining = order.qty - order.filled_qty
                fill_qty = max(1, remaining // 2) if remaining > 1 else remaining
                fee = self.cost_model.estimate_one_way_cost(bar.close, fill_qty)
                fill_px = self.cost_model.round_price(bar.close)
                s.add(FillRecord(order_id=order.id, symbol=order.symbol, side=order.side, qty=fill_qty, price=fill_px, fee=fee))
                order.filled_qty += fill_qty
                order.status = OrderStatus.FILLED.value if order.filled_qty >= order.qty else OrderStatus.PARTIALLY_FILLED.value
                self._apply_fill(s, order.symbol, order.side, fill_qty, fill_px, fee)
            s.commit()

    def _apply_fill(self, s: Session, symbol: str, side: str, qty: int, px: float, fee: float) -> None:
        position = s.get(PositionRecord, symbol)
        if position is None:
            position = PositionRecord(symbol=symbol, qty=0, avg_entry=0.0, last_price=px, realized_pnl=0.0)
            s.add(position)
            s.flush()
        position.last_price = px
        signed_qty = qty if side == OrderSide.BUY.value else -qty
        old_qty = position.qty
        new_qty = old_qty + signed_qty
        if old_qty == 0 or (old_qty > 0 and signed_qty > 0) or (old_qty < 0 and signed_qty < 0):
            total_notional = abs(old_qty) * position.avg_entry + qty * px
            position.qty = new_qty
            position.avg_entry = total_notional / max(abs(new_qty), 1)
            position.realized_pnl -= fee
            return
        close_qty = min(abs(old_qty), qty)
        pnl_per_share = (px - position.avg_entry) * (1 if old_qty > 0 else -1)
        realized = close_qty * pnl_per_share - fee
        position.realized_pnl += realized
        if new_qty == 0:
            s.add(ClosedTradeRecord(symbol=symbol, entry_price=position.avg_entry, exit_price=px, qty=close_qty, pnl=realized))
            position.avg_entry = 0.0
        position.qty = new_qty
