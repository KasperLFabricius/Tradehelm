"""Deterministic intraday VWAP pullback continuation strategy."""
from __future__ import annotations

from dataclasses import dataclass

from tradehelm.config.models import VwapStrategyConfig
from tradehelm.providers.interfaces import Strategy
from tradehelm.strategies.features import cumulative_vwap
from tradehelm.trading_engine.types import Bar, OrderSide, StrategyAction, StrategyIntent


@dataclass(slots=True)
class VwapSymbolState:
    session_day: str = ""
    history: list[Bar] = None  # type: ignore[assignment]
    position_side: OrderSide | None = None
    entry_price: float | None = None
    entry_bar_index: int | None = None
    pulled_back_long: bool = False
    pulled_back_short: bool = False

    def __post_init__(self) -> None:
        if self.history is None:
            self.history = []


class VwapContinuationStrategy(Strategy):
    strategy_id = "vwap"

    def __init__(self, config: VwapStrategyConfig | None = None) -> None:
        self.config = config or VwapStrategyConfig()
        self._state: dict[str, VwapSymbolState] = {}

    def _state_for(self, symbol: str) -> VwapSymbolState:
        if symbol not in self._state:
            self._state[symbol] = VwapSymbolState()
        return self._state[symbol]

    def _reset_session(self, st: VwapSymbolState, day: str) -> None:
        st.session_day = day
        st.history.clear()
        st.position_side = None
        st.entry_price = None
        st.entry_bar_index = None
        st.pulled_back_long = False
        st.pulled_back_short = False

    def _dir_ok(self, side: OrderSide) -> bool:
        return self.config.direction == "BOTH" or (self.config.direction == "LONG" and side == OrderSide.BUY) or (self.config.direction == "SHORT" and side == OrderSide.SELL)

    def on_bar(self, bar: Bar) -> list[StrategyIntent]:
        day = bar.ts.date().isoformat()
        st = self._state_for(bar.symbol)
        if st.session_day != day:
            self._reset_session(st, day)
        st.history.append(bar)
        idx = len(st.history) - 1

        vwap = cumulative_vwap(st.history)
        if vwap is None or len(st.history) < 4:
            return []

        if st.position_side is not None and st.entry_price is not None and st.entry_bar_index is not None:
            if st.position_side == OrderSide.BUY:
                if bar.low <= st.entry_price - self.config.stop_loss:
                    side, reason = OrderSide.SELL, "vwap_stop_exit"
                elif bar.high >= st.entry_price + self.config.take_profit:
                    side, reason = OrderSide.SELL, "vwap_target_exit"
                elif idx - st.entry_bar_index >= self.config.max_bars_in_trade:
                    side, reason = OrderSide.SELL, "vwap_max_bars_exit"
                else:
                    return []
            else:
                if bar.high >= st.entry_price + self.config.stop_loss:
                    side, reason = OrderSide.BUY, "vwap_stop_exit"
                elif bar.low <= st.entry_price - self.config.take_profit:
                    side, reason = OrderSide.BUY, "vwap_target_exit"
                elif idx - st.entry_bar_index >= self.config.max_bars_in_trade:
                    side, reason = OrderSide.BUY, "vwap_max_bars_exit"
                else:
                    return []
            st.position_side = None
            st.entry_price = None
            st.entry_bar_index = None
            return [StrategyIntent(bar.symbol, side, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason=reason)]

        prev = st.history[-2]
        was_above = prev.close > vwap
        now_above = bar.close > vwap
        pullback = abs(bar.close - vwap)

        if was_above and bar.low <= vwap + self.config.pullback_threshold:
            st.pulled_back_long = True
        if (not was_above) and bar.high >= vwap - self.config.pullback_threshold:
            st.pulled_back_short = True

        if st.pulled_back_long and now_above and pullback >= self.config.reentry_buffer and self._dir_ok(OrderSide.BUY):
            st.pulled_back_long = False
            st.position_side = OrderSide.BUY
            st.entry_price = bar.close
            st.entry_bar_index = idx
            return [StrategyIntent(bar.symbol, OrderSide.BUY, self.config.qty, StrategyAction.ENTRY, self.strategy_id, reason="vwap_pullback_entry", metadata={"vwap": round(vwap, 4)})]

        if st.pulled_back_short and (not now_above) and pullback >= self.config.reentry_buffer and self._dir_ok(OrderSide.SELL):
            st.pulled_back_short = False
            st.position_side = OrderSide.SELL
            st.entry_price = bar.close
            st.entry_bar_index = idx
            return [StrategyIntent(bar.symbol, OrderSide.SELL, self.config.qty, StrategyAction.ENTRY, self.strategy_id, reason="vwap_pullback_entry", metadata={"vwap": round(vwap, 4)})]

        return []

    def status(self) -> dict:
        tracked = {sym: {"session_day": st.session_day, "in_position": st.position_side is not None} for sym, st in self._state.items()}
        return {"config": self.config.model_dump(), "tracked_symbols": tracked}
