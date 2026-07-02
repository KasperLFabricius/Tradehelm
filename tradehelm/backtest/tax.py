"""Danish aktieindkomst tax model (docs/COSTS_AND_TAX.md section 2).

Individual, listed shares, realisation principle. Key rules:
- Average-cost basis (gennemsnitsmetoden): a sale consumes at the per-instrument
  average purchase price over all shares currently held.
- Realized gains/losses (and dividends) accumulate per calendar year.
- Year-end: prior-year carried losses are consumed first, then the progression
  bands apply (rate_low up to the year's threshold, rate_high above).
- Loss ring-fencing: a negative year yields zero tax and carries forward
  indefinitely, offsetting only future listed-share gains/dividends.

All amounts are DKK and FX-inclusive: callers pass per-share cost/proceeds already
converted at the trade-date USD/DKK rate, so FX movement is part of the gain.
"""

from __future__ import annotations

from collections import defaultdict


def progressive_tax(taxable: float, threshold: float, rate_low: float, rate_high: float) -> float:
    """Two-band aktieindkomst tax on a positive taxable amount (DKK)."""
    if taxable <= 0:
        return 0.0
    low = min(taxable, threshold)
    high = max(0.0, taxable - threshold)
    return rate_low * low + rate_high * high


class DanishTaxLedger:
    def __init__(
        self,
        thresholds: dict[int, float],
        rate_low: float = 0.27,
        rate_high: float = 0.42,
    ) -> None:
        self._thresholds = dict(thresholds)
        self.rate_low = rate_low
        self.rate_high = rate_high
        self._basis: dict[str, tuple[float, float]] = {}  # symbol -> (shares, total_cost_dkk)
        self._realized: dict[int, float] = defaultdict(float)  # year -> net realized DKK
        self._closed: dict[int, float] = {}  # settled year -> tax due (idempotency)
        self.carried_loss = 0.0  # unused prior-year losses (non-negative)

    def buy(self, symbol: str, shares: float, price_dkk: float) -> None:
        if shares <= 0:
            raise ValueError(f"buy quantity must be positive, got {shares}")
        held, cost = self._basis.get(symbol, (0.0, 0.0))
        self._basis[symbol] = (held + shares, cost + shares * price_dkk)

    def sell(self, symbol: str, shares: float, price_dkk: float, year: int) -> float:
        """Realize a sale at the average cost; returns the DKK gain/loss."""
        if shares <= 0:
            raise ValueError(f"sell quantity must be positive, got {shares}")
        held, cost = self._basis.get(symbol, (0.0, 0.0))
        if shares > held + 1e-9:
            raise ValueError(f"cannot sell {shares} of {symbol!r}; only {held} held")
        avg = cost / held if held else 0.0
        gain = (price_dkk - avg) * shares
        self._basis[symbol] = (held - shares, cost - avg * shares)
        self._realized[year] += gain
        return gain

    def add_dividend(self, amount_dkk: float, year: int) -> None:
        """Record dividend income (or, with a negative amount, other realized P&L)."""
        self._realized[year] += amount_dkk

    def average_cost(self, symbol: str) -> float:
        held, cost = self._basis.get(symbol, (0.0, 0.0))
        return cost / held if held else 0.0

    def shares(self, symbol: str) -> float:
        return self._basis.get(symbol, (0.0, 0.0))[0]

    def threshold(self, year: int) -> float:
        return self._thresholds[year]  # KeyError = fail loud on an unconfigured year

    def realized(self, year: int) -> float:
        return self._realized.get(year, 0.0)

    def close_year(self, year: int) -> float:
        """Finalize a calendar year: update carried losses, return the DKK tax due.

        Idempotent: settling an already-closed year returns the same tax without
        touching carried losses again (so a report replaying settlement is safe).
        """
        if year in self._closed:
            return self._closed[year]

        net = self._realized.get(year, 0.0)
        if net < 0:
            self.carried_loss += -net
            tax = 0.0
        elif net - self.carried_loss <= 0:
            self.carried_loss -= net  # this year's gain partly consumed the carry
            tax = 0.0
        else:
            # Compute (may raise on an unconfigured year) BEFORE mutating carry, so a
            # failure leaves the ledger unchanged.
            tax = progressive_tax(
                net - self.carried_loss, self.threshold(year), self.rate_low, self.rate_high
            )
            self.carried_loss = 0.0

        self._closed[year] = tax
        return tax
