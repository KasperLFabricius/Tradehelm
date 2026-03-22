"""Deterministic opening-range breakout strategy with lifecycle exits."""
from __future__ import annotations

from dataclasses import dataclass

from tradehelm.config.models import OrbStrategyConfig
from tradehelm.providers.interfaces import Strategy
from tradehelm.strategies.features import opening_range
from tradehelm.trading_engine.types import Bar, OrderSide, StrategyAction, StrategyIntent


@dataclass(slots=True)
class OrbSymbolState:
    session_day: str = ""
    history: list[Bar] = None  # type: ignore[assignment]
    opening_high: float | None = None
    opening_low: float | None = None
    breakout_fired: bool = False
    position_side: OrderSide | None = None
    entry_price: float | None = None
    entry_bar_index: int | None = None

    def __post_init__(self) -> None:
        if self.history is None:
            self.history = []


class OpeningRangeBreakoutStrategy(Strategy):
    """Opening range breakout with one entry/session and deterministic exits."""

    strategy_id = "orb"

    def __init__(self, config: OrbStrategyConfig | None = None) -> None:
        self.config = config or OrbStrategyConfig()
        self._state: dict[str, OrbSymbolState] = {}

    def _state_for(self, symbol: str) -> OrbSymbolState:
        if symbol not in self._state:
            self._state[symbol] = OrbSymbolState()
        return self._state[symbol]

    def _reset_session(self, st: OrbSymbolState, day: str) -> None:
        st.session_day = day
        st.history.clear()
        st.opening_high = None
        st.opening_low = None
        st.breakout_fired = False
        st.position_side = None
        st.entry_price = None
        st.entry_bar_index = None

    def _in_direction(self, side: OrderSide) -> bool:
        return self.config.direction == "BOTH" or (self.config.direction == "LONG" and side == OrderSide.BUY) or (self.config.direction == "SHORT" and side == OrderSide.SELL)

    def on_bar(self, bar: Bar) -> list[StrategyIntent]:
        day = bar.ts.date().isoformat()
        st = self._state_for(bar.symbol)
        if st.session_day != day:
            self._reset_session(st, day)

        st.history.append(bar)
        idx = len(st.history) - 1
        intents: list[StrategyIntent] = []

        if st.position_side is not None and st.entry_price is not None and st.entry_bar_index is not None:
            if st.position_side == OrderSide.BUY:
                if bar.low <= st.entry_price - self.config.stop_loss:
                    intents.append(StrategyIntent(bar.symbol, OrderSide.SELL, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="orb_stop_exit"))
                elif bar.high >= st.entry_price + self.config.take_profit:
                    intents.append(StrategyIntent(bar.symbol, OrderSide.SELL, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="orb_target_exit"))
            else:
                if bar.high >= st.entry_price + self.config.stop_loss:
                    intents.append(StrategyIntent(bar.symbol, OrderSide.BUY, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="orb_stop_exit"))
                elif bar.low <= st.entry_price - self.config.take_profit:
                    intents.append(StrategyIntent(bar.symbol, OrderSide.BUY, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="orb_target_exit"))

            if not intents and (idx - st.entry_bar_index) >= self.config.max_bars_in_trade:
                exit_side = OrderSide.SELL if st.position_side == OrderSide.BUY else OrderSide.BUY
                intents.append(StrategyIntent(bar.symbol, exit_side, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="orb_max_bars_exit"))

            if not intents and self.config.flatten_end_of_session and idx >= 77:
                exit_side = OrderSide.SELL if st.position_side == OrderSide.BUY else OrderSide.BUY
                intents.append(StrategyIntent(bar.symbol, exit_side, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="orb_session_flatten"))

            if intents:
                st.position_side = None
                st.entry_price = None
                st.entry_bar_index = None
                return intents

        rng = opening_range(st.history, self.config.opening_range_bars)
        if rng is None:
            return []
        st.opening_high, st.opening_low = rng
        if st.breakout_fired or st.position_side is not None:
            return []

        up_level = st.opening_high + self.config.breakout_buffer
        dn_level = st.opening_low - self.config.breakout_buffer
        if bar.close >= up_level and self._in_direction(OrderSide.BUY):
            st.breakout_fired = True
            st.position_side = OrderSide.BUY
            st.entry_price = bar.close
            st.entry_bar_index = idx
            return [
                StrategyIntent(
                    symbol=bar.symbol,
                    side=OrderSide.BUY,
                    qty=self.config.qty,
                    action=StrategyAction.ENTRY,
                    strategy_id=self.strategy_id,
                    reason="orb_breakout_long",
                    metadata={"opening_high": st.opening_high, "buffer": self.config.breakout_buffer},
                )
            ]
        if bar.close <= dn_level and self._in_direction(OrderSide.SELL):
            st.breakout_fired = True
            st.position_side = OrderSide.SELL
            st.entry_price = bar.close
            st.entry_bar_index = idx
            return [
                StrategyIntent(
                    symbol=bar.symbol,
                    side=OrderSide.SELL,
                    qty=self.config.qty,
                    action=StrategyAction.ENTRY,
                    strategy_id=self.strategy_id,
                    reason="orb_breakout_short",
                    metadata={"opening_low": st.opening_low, "buffer": self.config.breakout_buffer},
                )
            ]
        return []

    def status(self) -> dict:
        tracked = {sym: {"session_day": st.session_day, "breakout_fired": st.breakout_fired, "in_position": st.position_side is not None} for sym, st in self._state.items()}
        return {"config": self.config.model_dump(), "tracked_symbols": tracked}
