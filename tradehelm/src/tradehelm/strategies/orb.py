"""Very simple opening range breakout demo strategy."""
from __future__ import annotations

from collections import defaultdict

from tradehelm.providers.interfaces import Strategy
from tradehelm.trading_engine.types import Bar, OrderSide


class OpeningRangeBreakoutStrategy(Strategy):
    """Breakout after first 3 bars per symbol."""

    strategy_id = "orb"

    def __init__(self, qty: int = 10) -> None:
        self.qty = qty
        self._history: dict[str, list[Bar]] = defaultdict(list)

    def on_bar(self, bar: Bar) -> list[dict]:
        hist = self._history[bar.symbol]
        hist.append(bar)
        if len(hist) < 4:
            return []
        opening = hist[:3]
        high = max(b.high for b in opening)
        low = min(b.low for b in opening)
        if bar.close > high:
            return [{"symbol": bar.symbol, "side": OrderSide.BUY, "qty": self.qty}]
        if bar.close < low:
            return [{"symbol": bar.symbol, "side": OrderSide.SELL, "qty": self.qty}]
        return []
