"""Risk engine enforcing layered risk checks."""
from __future__ import annotations

from collections import defaultdict

from tradehelm.config.models import RiskConfig


class RiskEngine:
    """Applies configured risk limits to candidate orders."""

    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.trades_today = 0
        self.cooldown_left: dict[str, int] = defaultdict(int)

    def on_bar(self) -> None:
        """Decrement symbol cooldown counters."""
        for symbol in list(self.cooldown_left):
            self.cooldown_left[symbol] = max(0, self.cooldown_left[symbol] - 1)

    def on_exit(self, symbol: str) -> None:
        """Set cooldown on symbol after position exit."""
        self.cooldown_left[symbol] = self.config.cooldown_bars_after_exit

    def validate(
        self,
        symbol: str,
        qty: int,
        price: float,
        estimated_edge: float,
        daily_pnl: float,
        current_positions: int,
    ) -> tuple[bool, str]:
        """Return (allowed, reason)."""
        if daily_pnl <= -self.config.max_daily_loss:
            return False, "max daily loss breached"
        if current_positions >= self.config.max_simultaneous_positions:
            return False, "max simultaneous positions reached"
        if qty > self.config.max_position_size:
            return False, "max position size exceeded"
        if self.trades_today >= self.config.max_trades_per_day:
            return False, "max trades per day reached"
        if self.cooldown_left[symbol] > 0:
            return False, "symbol cooldown active"
        if estimated_edge <= 0:
            return False, "non-positive net edge after friction"
        if price * qty * 0.01 > self.config.max_risk_per_trade:
            return False, "max risk per trade exceeded"
        return True, "ok"
