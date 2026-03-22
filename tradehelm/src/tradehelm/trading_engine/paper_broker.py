"""Paper broker with simple fill simulation and friction."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tradehelm.persistence.db import ClosedTradeRecord, FillRecord, OrderRecord, PositionRecord
from tradehelm.providers.interfaces import BrokerProvider
from tradehelm.trading_engine.cost_model import GenericCostModel
from tradehelm.trading_engine.types import Bar, OrderSide, OrderStatus, OrderType


class PaperBroker(BrokerProvider):
    """Simulation broker for market/limit order handling."""

    def __init__(
        self,
        session_factory: sessionmaker,
        cost_model: GenericCostModel,
        on_position_closed: Callable[[str], None] | None = None,
        on_realized_pnl: Callable[[float], None] | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.cost_model = cost_model
        self.last_prices: dict[str, float] = {}
        self.on_position_closed = on_position_closed
        self.on_realized_pnl = on_realized_pnl

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

    def _fill_price(self, order: OrderRecord, bar: Bar) -> float:
        side = OrderSide(order.side)
        if order.order_type == OrderType.MARKET.value:
            return self.cost_model.adjusted_fill_price(bar.close, side)
        ref = min(order.limit_price or bar.close, bar.close) if side == OrderSide.BUY else max(order.limit_price or bar.close, bar.close)
        return self.cost_model.round_price(ref)

    def on_bar(self, bar: Bar) -> None:
        self.last_prices[bar.symbol] = bar.close
        with self.session_factory() as s:
            position = s.get(PositionRecord, bar.symbol)
            if position is not None:
                position.last_price = self.cost_model.round_price(bar.close)

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
                fill_px = self._fill_price(order, bar)
                fee = self.cost_model.estimate_one_way_cost(fill_px, fill_qty)
                s.add(FillRecord(order_id=order.id, symbol=order.symbol, side=order.side, qty=fill_qty, price=fill_px, fee=fee, ts=bar.ts))
                order.filled_qty += fill_qty
                order.status = OrderStatus.FILLED.value if order.filled_qty >= order.qty else OrderStatus.PARTIALLY_FILLED.value
                self._apply_fill(s, order.symbol, OrderSide(order.side), fill_qty, fill_px, fee)
            s.commit()

    def force_flatten_symbol(self, symbol: str, ts: datetime, reference_price: float | None = None) -> None:
        """Force-close one open position and persist audit records."""
        with self.session_factory() as s:
            position = s.get(PositionRecord, symbol)
            if position is None or position.qty == 0:
                return
            qty = abs(position.qty)
            side = OrderSide.SELL if position.qty > 0 else OrderSide.BUY
            ref_px = reference_price if reference_price is not None else position.last_price
            fill_px = self.cost_model.adjusted_fill_price(ref_px, side)
            fee = self.cost_model.estimate_one_way_cost(fill_px, qty)
            synthetic_id = f"kill-{uuid4()}"
            s.add(FillRecord(order_id=synthetic_id, symbol=symbol, side=side.value, qty=qty, price=fill_px, fee=fee, ts=ts))
            self._apply_fill(s, symbol, side, qty, fill_px, fee)
            position = s.get(PositionRecord, symbol)
            if position is not None:
                position.last_price = fill_px
            s.commit()

    def _apply_fill(self, s: Session, symbol: str, side: OrderSide, qty: int, px: float, fee: float) -> None:
        position = s.get(PositionRecord, symbol)
        if position is None:
            position = PositionRecord(symbol=symbol, qty=0, avg_entry=0.0, last_price=px, realized_pnl=0.0)
            s.add(position)
            s.flush()

        old_qty = position.qty
        signed_qty = qty if side == OrderSide.BUY else -qty
        new_qty = old_qty + signed_qty
        position.last_price = px

        if old_qty == 0 or (old_qty > 0 and signed_qty > 0) or (old_qty < 0 and signed_qty < 0):
            total_notional = abs(old_qty) * position.avg_entry + qty * px
            position.qty = new_qty
            position.avg_entry = total_notional / abs(new_qty)
            delta = -fee
            position.realized_pnl += delta
            if self.on_realized_pnl:
                self.on_realized_pnl(delta)
            return

        close_qty = min(abs(old_qty), qty)
        pnl_per_share = (px - position.avg_entry) if old_qty > 0 else (position.avg_entry - px)
        delta = (close_qty * pnl_per_share) - fee
        position.realized_pnl += delta
        if self.on_realized_pnl:
            self.on_realized_pnl(delta)

        if new_qty == 0:
            s.add(ClosedTradeRecord(symbol=symbol, entry_price=position.avg_entry, exit_price=px, qty=close_qty, pnl=delta))
            position.qty = 0
            position.avg_entry = 0.0
            if self.on_position_closed:
                self.on_position_closed(symbol)
            return

        if (old_qty > 0 > new_qty) or (old_qty < 0 < new_qty):
            s.add(ClosedTradeRecord(symbol=symbol, entry_price=position.avg_entry, exit_price=px, qty=abs(old_qty), pnl=delta))
            position.qty = new_qty
            position.avg_entry = px
            if self.on_position_closed:
                self.on_position_closed(symbol)
            return

        position.qty = new_qty
