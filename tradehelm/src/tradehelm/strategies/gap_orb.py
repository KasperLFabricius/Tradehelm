"""Gap-filtered opening-range breakout strategy."""
from __future__ import annotations

from dataclasses import dataclass

from tradehelm.config.models import GapOrbStrategyConfig
from tradehelm.providers.interfaces import Strategy
from tradehelm.strategies.features import opening_range
from tradehelm.trading_engine.types import Bar, OrderSide, StrategyAction, StrategyIntent


@dataclass(slots=True)
class GapOrbSymbolState:
    session_day: str = ""
    history: list[Bar] = None  # type: ignore[assignment]
    prior_close: float | None = None
    opening_high: float | None = None
    opening_low: float | None = None
    setup_ok: bool = False
    breakout_fired: bool = False
    position_side: OrderSide | None = None
    entry_price: float | None = None
    entry_bar_index: int | None = None

    def __post_init__(self) -> None:
        if self.history is None:
            self.history = []


class GapFilteredOrbStrategy(Strategy):
    strategy_id = "gap_orb"

    def __init__(self, config: GapOrbStrategyConfig | None = None) -> None:
        self.config = config or GapOrbStrategyConfig()
        self._state: dict[str, GapOrbSymbolState] = {}

    def _state_for(self, symbol: str) -> GapOrbSymbolState:
        if symbol not in self._state:
            self._state[symbol] = GapOrbSymbolState()
        return self._state[symbol]

    def _reset_session(self, st: GapOrbSymbolState, day: str) -> None:
        if st.history:
            st.prior_close = st.history[-1].close
        st.session_day = day
        st.history.clear()
        st.opening_high = None
        st.opening_low = None
        st.setup_ok = False
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

        if st.position_side is not None and st.entry_price is not None and st.entry_bar_index is not None:
            return self._manage_position(st, bar, idx)

        rng = opening_range(st.history, self.config.opening_range_bars)
        if rng is None:
            return []
        st.opening_high, st.opening_low = rng
        if st.breakout_fired:
            return []

        first_bar = st.history[0]
        prior_close = st.prior_close if st.prior_close is not None else first_bar.open
        gap_pct = abs((first_bar.open - prior_close) / max(abs(prior_close), 1e-9)) * 100.0
        opening_range_size = st.opening_high - st.opening_low
        opening_range_pct = (opening_range_size / max(abs(prior_close), 1e-9)) * 100.0
        setup_ok = gap_pct >= self.config.min_gap_pct and opening_range_pct >= self.config.min_opening_range_pct and first_bar.volume >= self.config.min_first_bar_volume
        st.setup_ok = setup_ok
        if not setup_ok:
            return []

        up_level = st.opening_high + self.config.breakout_buffer
        dn_level = st.opening_low - self.config.breakout_buffer
        if bar.close >= up_level and self._in_direction(OrderSide.BUY):
            return [StrategyIntent(bar.symbol, OrderSide.BUY, self.config.qty, StrategyAction.ENTRY, self.strategy_id, reason="gap_orb_breakout_long")]
        if bar.close <= dn_level and self._in_direction(OrderSide.SELL):
            return [StrategyIntent(bar.symbol, OrderSide.SELL, self.config.qty, StrategyAction.ENTRY, self.strategy_id, reason="gap_orb_breakout_short")]
        return []

    def _manage_position(self, st: GapOrbSymbolState, bar: Bar, idx: int) -> list[StrategyIntent]:
        intents: list[StrategyIntent] = []
        assert st.entry_price is not None and st.entry_bar_index is not None and st.position_side is not None
        if st.position_side == OrderSide.BUY:
            if bar.low <= st.entry_price - self.config.stop_loss:
                intents.append(StrategyIntent(bar.symbol, OrderSide.SELL, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="gap_orb_stop_exit"))
            elif bar.high >= st.entry_price + self.config.take_profit:
                intents.append(StrategyIntent(bar.symbol, OrderSide.SELL, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="gap_orb_target_exit"))
        else:
            if bar.high >= st.entry_price + self.config.stop_loss:
                intents.append(StrategyIntent(bar.symbol, OrderSide.BUY, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="gap_orb_stop_exit"))
            elif bar.low <= st.entry_price - self.config.take_profit:
                intents.append(StrategyIntent(bar.symbol, OrderSide.BUY, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="gap_orb_target_exit"))

        if not intents and idx - st.entry_bar_index >= self.config.max_bars_in_trade:
            side = OrderSide.SELL if st.position_side == OrderSide.BUY else OrderSide.BUY
            intents.append(StrategyIntent(bar.symbol, side, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="gap_orb_max_bars_exit"))
        if not intents and self.config.flatten_end_of_session and idx >= 77:
            side = OrderSide.SELL if st.position_side == OrderSide.BUY else OrderSide.BUY
            intents.append(StrategyIntent(bar.symbol, side, self.config.qty, StrategyAction.EXIT, self.strategy_id, reason="gap_orb_session_flatten"))
        return intents

    def on_entry_accepted(self, intent: StrategyIntent, bar: Bar) -> None:
        st = self._state_for(intent.symbol)
        st.breakout_fired = True
        st.position_side = intent.side
        st.entry_price = bar.close
        st.entry_bar_index = len(st.history) - 1

    def on_exit_accepted(self, intent: StrategyIntent, bar: Bar) -> None:
        st = self._state_for(intent.symbol)
        st.position_side = None
        st.entry_price = None
        st.entry_bar_index = None

    def status(self) -> dict:
        return {"config": self.config.model_dump()}
