"""Buy-and-hold benchmark, run through the same cost+tax engine as the candidates
so comparisons are apples-to-apples (STRATEGY_SPEC.md section "Validation protocol").

The research harness runs this with a single-symbol universe (default SPY) so the
engine's point-in-time membership check does not drop the target.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..backtest.engine import StrategyContext, TargetPosition


@dataclass
class BuyAndHold:
    """Buy `symbol` once with all equity, then hold it untouched (no stop)."""

    symbol: str = "SPY"
    name: str = "buy_and_hold"

    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]:
        if ctx.portfolio.held(self.symbol) > 0:
            return [TargetPosition(self.symbol, weight=None, reason="bh-hold")]
        if ctx.close(self.symbol) is None:
            return []  # no price yet -> wait
        return [TargetPosition(self.symbol, weight=1.0, reason="bh-entry")]
