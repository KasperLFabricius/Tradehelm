"""Shared machinery for the v1 strategy candidates: risk parameters, the resting
liquidity/regime filters, per-position bookkeeping (entry price / ATR / high-water
mark, needed for entry-relative stops), risk-based sizing, and slot allocation.

Nothing here reads data beyond the strategy's decision date; every input is a frame
already sliced at that date by StrategyContext, or the market calendar (a public
schedule, not market data). No LLM/API calls (CLAUDE.md rule 1).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..backtest.engine import StrategyContext
from ..config.models import RiskConfig


@dataclass(frozen=True)
class RiskParams:
    """Sizing/slot limits, mirrored from RiskConfig (ARCHITECTURE.md section 5)."""

    max_positions: int = 10
    per_position_risk_frac: float = 0.01
    max_position_notional_frac: float = 0.20
    min_ticket_dkk: float = 0.0  # engine skips buys below this DKK notional (F3)

    @classmethod
    def from_config(cls, cfg: RiskConfig) -> RiskParams:
        return cls(
            max_positions=cfg.max_positions,
            per_position_risk_frac=cfg.per_position_risk_frac,
            max_position_notional_frac=cfg.max_position_notional_frac,
            min_ticket_dkk=cfg.min_ticket_dkk,
        )


@dataclass
class Lot:
    """What we remember about an open position for entry-relative stops."""

    entry_date: pd.Timestamp
    entry_price: float  # fill proxy: the open of the session we first saw the position
    entry_atr: float
    high_since: float  # highest close since entry (for trailing stops)
    stop: float | None = None  # last resting stop we placed (stops only ratchet up)


class PositionBook:
    """Tracks open lots and reconciles them against the portfolio each session.

    The engine, not the strategy, decides fills, so the portfolio is authoritative:
    a symbol that appears is a new lot (entered at this session's open); one that
    vanishes was exited or stopped and is forgotten. This keeps strategy state from
    drifting away from reality across a long run.
    """

    def __init__(self) -> None:
        self._lots: dict[str, Lot] = {}

    def sync(self, ctx: StrategyContext) -> None:
        held = {s for s, q in ctx.portfolio.shares.items() if q > 0}
        for symbol in list(self._lots):
            if symbol not in held:
                del self._lots[symbol]  # exited / stopped out
        for symbol in held:
            close = ctx.close(symbol)
            if close is None:
                continue
            if symbol in self._lots:
                lot = self._lots[symbol]
                lot.high_since = max(lot.high_since, close)
            else:
                entry_open = ctx.value(symbol, "open")
                atr = ctx.feature(symbol, "atr14")
                self._lots[symbol] = Lot(
                    entry_date=ctx.date,
                    entry_price=entry_open if entry_open is not None else close,
                    entry_atr=atr if atr is not None else float("nan"),
                    high_since=close,
                )

    def lot(self, symbol: str) -> Lot | None:
        return self._lots.get(symbol)

    def days_held(self, symbol: str, ctx: StrategyContext) -> int:
        lot = self._lots.get(symbol)
        if lot is None:
            return 0
        return ctx.sessions_since(symbol, lot.entry_date)

    def ratchet_stop(self, symbol: str, new_stop: float) -> float:
        """Raise (never lower) the resting stop and return the effective value."""
        lot = self._lots.get(symbol)
        if lot is None:
            return new_stop
        lot.stop = new_stop if lot.stop is None else max(lot.stop, new_stop)
        return lot.stop


@dataclass
class EntrySignal:
    symbol: str
    entry_ref: float  # decision close (fill-price proxy for sizing/stop)
    stop: float
    rank_key: float  # sorted ascending; candidates negate for "highest first"
    weight: float | None = None  # explicit weight (Candidate C); None = risk-based sizing


def _isnan(x: float) -> bool:
    return x != x


def regime_ok(ctx: StrategyContext, symbol: str, sma_window: int) -> bool:
    """True when the regime proxy (e.g. SPY) closes above its SMA(sma_window)."""
    c = ctx.close(symbol)
    ma = ctx.feature(symbol, f"sma{sma_window}")
    if c is None or ma is None:
        return False
    return c > ma


def liquidity_ok(
    ctx: StrategyContext, symbol: str, min_price: float, min_dollar_volume: float
) -> bool:
    """Price and 20-day median dollar-volume filter from STRATEGY_SPEC.md."""
    c = ctx.close(symbol)
    if c is None or c <= min_price:
        return False
    mdv = ctx.feature(symbol, "mdv20")
    return mdv is not None and mdv > min_dollar_volume


def risk_weight(entry_ref: float, stop: float, risk_frac: float, max_notional_frac: float) -> float:
    """Fraction of equity to allocate so a stop-out loses ~`risk_frac` of equity.

    weight = risk_frac * price / (price - stop), capped at `max_notional_frac`. The
    engine multiplies this by equity to get whole shares, reproducing the spec's
    shares = (equity * risk_frac) / (entry - stop). Zero if the stop is not below
    the entry (an ill-posed trade).
    """
    if entry_ref <= 0 or stop < 0 or stop >= entry_ref:
        return 0.0
    weight = risk_frac * entry_ref / (entry_ref - stop)
    return min(weight, max_notional_frac)


def current_exposure_frac(ctx: StrategyContext, keep: set[str]) -> float:
    """Marked value of the positions we are keeping, as a fraction of equity."""
    cash = ctx.portfolio.cash_usd
    invested = 0.0
    total = cash
    for symbol, shares in ctx.portfolio.shares.items():
        price = ctx.close(symbol)
        if price is None:
            continue
        value = shares * price
        total += value
        if symbol in keep:
            invested += value
    if total <= 0:
        return 0.0
    return invested / total


def allocate_new_entries(
    ranked: list[EntrySignal], risk: RiskParams, n_kept: int, existing_exposure: float
) -> list[tuple[str, float, float]]:
    """Turn ranked entry signals into (symbol, weight, stop), respecting the free
    slot count and keeping total intended exposure <= 1 (long-only, no leverage).

    Signals must already be sorted best-first; once a name would breach full
    investment we stop (rather than skip it to cherry-pick a smaller lower-ranked
    one, which would violate the ranking)."""
    out: list[tuple[str, float, float]] = []
    slots = risk.max_positions - n_kept
    exposure = existing_exposure
    for sig in ranked:
        if slots <= 0:
            break
        # Candidate C supplies an explicit equal-ish weight (Fable review F2); A and B
        # leave it None and size by risk (ATR stop distance).
        weight = (
            sig.weight
            if sig.weight is not None
            else risk_weight(
                sig.entry_ref,
                sig.stop,
                risk.per_position_risk_frac,
                risk.max_position_notional_frac,
            )
        )
        if weight <= 0.0:
            continue
        if exposure + weight > 1.0:
            break
        out.append((sig.symbol, weight, sig.stop))
        exposure += weight
        slots -= 1
    return out


def is_week_end_session(calendar, day: pd.Timestamp) -> bool:
    """True if `day` is the last trading session of its ISO week (calendar-based,
    so holidays that move the weekly close off Friday are handled)."""
    day = pd.Timestamp(day).normalize()
    nxt = calendar.next_session(day)
    y1, w1, _ = day.isocalendar()
    y2, w2, _ = pd.Timestamp(nxt).isocalendar()
    return (y1, w1) != (y2, w2)


# Default resting filter thresholds from STRATEGY_SPEC.md (price > 5 USD, 20-day
# median dollar volume > 10M USD).
MIN_PRICE_USD: float = 5.0
MIN_DOLLAR_VOLUME_USD: float = 10_000_000.0
REGIME_SYMBOL: str = "SPY"
REGIME_SMA: int = 200


@dataclass
class _CandidateBase:
    """Common construction: parameters + shared risk/filter knobs. Subclasses set
    `name` and implement target_positions."""

    risk: RiskParams = field(default_factory=RiskParams)
    regime_symbol: str = REGIME_SYMBOL
    min_price: float = MIN_PRICE_USD
    min_dollar_volume: float = MIN_DOLLAR_VOLUME_USD
    book: PositionBook = field(default_factory=PositionBook)
