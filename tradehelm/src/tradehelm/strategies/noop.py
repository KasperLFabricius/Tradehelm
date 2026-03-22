"""No-op strategy for control-plane validation."""
from tradehelm.providers.interfaces import Strategy
from tradehelm.trading_engine.types import Bar


class NoOpStrategy(Strategy):
    """Never emits intents."""

    strategy_id = "noop"

    def on_bar(self, bar: Bar) -> list[dict]:
        return []
