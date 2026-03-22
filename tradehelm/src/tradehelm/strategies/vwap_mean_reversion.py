"""Deterministic VWAP mean-reversion strategy."""
from __future__ import annotations

from dataclasses import dataclass

from tradehelm.config.models import VwapMeanReversionStrategyConfig
from tradehelm.providers.interfaces import Strategy
from tradehelm.strategies.features import cumulative_vwap
from tradehelm.trading_engine.types import Bar, OrderSide, StrategyAction, StrategyIntent


@dataclass(slots=True)
class MeanRevState:
    session_day: str = ""
    history: list[Bar] = None  # type: ignore[assignment]
    position_side: OrderSide | None = None
    entry_price: float | None = None
    entry_bar_index: int | None = None
    stretched_long: bool = False
    stretched_short: bool = False

    def __post_init__(self) -> None:
        if self.history is None:
            self.history = []


class VwapMeanReversionStrategy(Strategy):
    strategy_id = "vwap_mean_reversion"

    def __init__(self, config: VwapMeanReversionStrategyConfig | None = None) -> None:
        self.config = config or VwapMeanReversionStrategyConfig()
        self._state: dict[str, MeanRevState] = {}

    def _state_for(self, symbol: str) -> MeanRevState:
        if symbol not in self._state:
            self._state[symbol] = MeanRevState()
        return self._state[symbol]

    def _dir_ok(self, side: OrderSide) -> bool:
        return self.config.direction == "BOTH" or (self.config.direction == "LONG" and side == OrderSide.BUY) or (self.config.direction == "SHORT" and side == OrderSide.SELL)

    def on_bar(self, bar: Bar) -> list[StrategyIntent]:
        st = self._state_for(bar.symbol)
        day = bar.ts.date().isoformat()
        if st.session_day != day:
            st.session_day = day
            st.history.clear()
            st.position_side = None
            st.entry_price = None
            st.entry_bar_index = None
            st.stretched_long = False
            st.stretched_short = False
        st.history.append(bar)
        idx = len(st.history) - 1
        vwap = cumulative_vwap(st.history)
        if vwap is None:
            return []

        stretch = bar.close - vwap
        if stretch <= -self.config.stretch_threshold:
            st.stretched_long = True
        if stretch >= self.config.stretch_threshold:
            st.stretched_short = True

        if st.position_side is not None and st.entry_price is not None and st.entry_bar_index is not None:
            if st.position_side == OrderSide.BUY:
                if bar.low <= st.entry_price - self.config.stop_loss:
                    return [StrategyIntent(bar.symbol, OrderSide.SELL, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="vwap_mr_stop_exit")]
                if bar.high >= st.entry_price + self.config.take_profit:
                    return [StrategyIntent(bar.symbol, OrderSide.SELL, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="vwap_mr_target_exit")]
            else:
                if bar.high >= st.entry_price + self.config.stop_loss:
                    return [StrategyIntent(bar.symbol, OrderSide.BUY, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="vwap_mr_stop_exit")]
                if bar.low <= st.entry_price - self.config.take_profit:
                    return [StrategyIntent(bar.symbol, OrderSide.BUY, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="vwap_mr_target_exit")]
            if idx - st.entry_bar_index >= self.config.max_bars_in_trade:
                side = OrderSide.SELL if st.position_side == OrderSide.BUY else OrderSide.BUY
                return [StrategyIntent(bar.symbol, side, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="vwap_mr_max_bars_exit")]
            if self.config.flatten_end_of_session and idx >= 77:
                side = OrderSide.SELL if st.position_side == OrderSide.BUY else OrderSide.BUY
                return [StrategyIntent(bar.symbol, side, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="vwap_mr_session_flatten")]
            return []

        if st.stretched_long and stretch >= -self.config.reversion_confirmation_buffer and self._dir_ok(OrderSide.BUY):
            return [StrategyIntent(bar.symbol, OrderSide.BUY, self.config.qty, StrategyAction.ENTRY, self.strategy_id, reason="vwap_mr_revert_long")]
        if st.stretched_short and stretch <= self.config.reversion_confirmation_buffer and self._dir_ok(OrderSide.SELL):
            return [StrategyIntent(bar.symbol, OrderSide.SELL, self.config.qty, StrategyAction.ENTRY, self.strategy_id, reason="vwap_mr_revert_short")]
        return []

    def on_entry_accepted(self, intent: StrategyIntent, bar: Bar) -> None:
        st = self._state_for(intent.symbol)
        st.position_side = intent.side
        st.entry_price = bar.close
        st.entry_bar_index = len(st.history) - 1
        st.stretched_long = False
        st.stretched_short = False

    def on_exit_accepted(self, intent: StrategyIntent, bar: Bar) -> None:
        st = self._state_for(intent.symbol)
        st.position_side = None
        st.entry_price = None
        st.entry_bar_index = None

    def status(self) -> dict:
        return {"config": self.config.model_dump()}
