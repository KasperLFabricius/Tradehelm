"""Config-driven friction cost model with explicit vs implicit costs."""
from __future__ import annotations

from tradehelm.config.models import FrictionConfig
from tradehelm.providers.interfaces import CostModelProvider
from tradehelm.trading_engine.types import OrderSide


class GenericCostModel(CostModelProvider):
    """Cost model separating explicit commission from implicit price impact."""

    def __init__(self, config: FrictionConfig) -> None:
        self.config = config

    def round_price(self, px: float) -> float:
        """Round prices to configured tick size."""
        tick = self.config.tick_size
        return round(round(px / tick) * tick, 6)

    def estimate_commission(self, price: float, qty: int) -> float:
        """Estimate explicit broker commission for one fill."""
        turnover = price * qty
        return max(
            self.config.minimum_commission,
            self.config.commission_fixed + turnover * self.config.commission_rate,
        )

    def estimate_price_impact_bps(self) -> float:
        """Estimate one-way implicit price impact in bps (half-spread + slippage)."""
        return (self.config.assumed_spread_bps / 2.0) + self.config.assumed_slippage_bps

    def estimate_one_way_explicit_cost(self, price: float, qty: int) -> float:
        """Estimate one-way explicit trading cost (commission only)."""
        return self.estimate_commission(price, qty)

    def estimate_one_way_implicit_cost(self, price: float, qty: int) -> float:
        """Estimate one-way implicit cost as monetary price impact."""
        return price * qty * (self.estimate_price_impact_bps() / 10000)

    def estimate_one_way_cost(self, price: float, qty: int) -> float:
        """Compatibility helper for explicit one-way fee used in fill records."""
        return self.estimate_one_way_explicit_cost(price, qty)

    def estimate_round_trip_cost(self, price: float, qty: int) -> float:
        """Estimate all-in round-trip cost for pre-trade edge checks."""
        one_way_explicit = self.estimate_one_way_explicit_cost(price, qty)
        one_way_implicit = self.estimate_one_way_implicit_cost(price, qty)
        return 2 * (one_way_explicit + one_way_implicit)

    def adjusted_fill_price(self, reference_price: float, side: OrderSide) -> float:
        """Apply implicit impact to execution price (worse for both buy and sell)."""
        multiplier = 1 + (self.estimate_price_impact_bps() / 10000)
        raw = reference_price * multiplier if side == OrderSide.BUY else reference_price / multiplier
        return self.round_price(raw)
