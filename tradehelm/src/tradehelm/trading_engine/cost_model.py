"""Config-driven friction cost model."""
from __future__ import annotations

from tradehelm.config.models import FrictionConfig
from tradehelm.providers.interfaces import CostModelProvider
from tradehelm.trading_engine.types import OrderSide


class GenericCostModel(CostModelProvider):
    """Simple cost model supporting commission, spread, slippage, and tick rounding."""

    def __init__(self, config: FrictionConfig) -> None:
        self.config = config

    def round_price(self, px: float) -> float:
        """Round prices to configured tick size."""
        tick = self.config.tick_size
        return round(round(px / tick) * tick, 6)

    def estimate_one_way_cost(self, price: float, qty: int) -> float:
        """Estimate one-way transaction friction."""
        turnover = price * qty
        commission = max(
            self.config.minimum_commission,
            self.config.commission_fixed + turnover * self.config.commission_rate,
        )
        spread = turnover * (self.config.assumed_spread_bps / 10000)
        slippage = turnover * (self.config.assumed_slippage_bps / 10000)
        return commission + spread + slippage

    def estimate_round_trip_cost(self, price: float, qty: int) -> float:
        """Estimate full open+close cost for expected value checks."""
        return 2 * self.estimate_one_way_cost(price, qty)

    def adjusted_fill_price(self, reference_price: float, side: OrderSide) -> float:
        """Apply directional spread/slippage impact to execution price."""
        impact_bps = (self.config.assumed_spread_bps / 2.0) + self.config.assumed_slippage_bps
        multiplier = 1 + (impact_bps / 10000)
        raw = reference_price * multiplier if side == OrderSide.BUY else reference_price / multiplier
        return self.round_price(raw)
