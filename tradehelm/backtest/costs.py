"""Saxo trading-cost model (docs/COSTS_AND_TAX.md section 1).

Per fill:
    fill_price = open * (1 + side * (half_spread_bps + slippage_bps) / 1e4)
    commission = max(min_commission, commission_rate * notional)   # per side
FX conversion is charged on DKK<->USD funding transfers, not per trade (D3).
"""

from __future__ import annotations

from tradehelm.config import CostConfig


class CostModel:
    def __init__(self, config: CostConfig) -> None:
        self._c = config

    def fill_price(self, open_price: float, side: int) -> float:
        """Executed price for a market-on-open order. side +1 buy, -1 sell."""
        if side not in (1, -1):
            raise ValueError("side must be +1 (buy) or -1 (sell)")
        bump = (self._c.half_spread_bps + self._c.slippage_bps) / 10_000.0
        return open_price * (1.0 + side * bump)

    def commission(self, notional: float) -> float:
        """Per-side commission in the instrument currency (USD)."""
        return max(self._c.min_commission_us, self._c.commission_rate_us * abs(notional))

    def fx_fee(self, amount: float) -> float:
        """FX conversion cost on a DKK<->USD funding transfer."""
        return self._c.fx_conversion_rate * abs(amount)
