"""Event-driven daily backtest engine (docs/ARCHITECTURE.md section 4).

Timing: the decision at session T uses data through T's (adjusted) close; orders
fill at T+1's adjusted open; protective stops are checked intraday on the fill
session; equity is marked at each session's adjusted close. No intrabar peeking.

Prices are total-return (adjusted): raw OHLC are scaled by adj_close/close so dividends
are baked into price. This is a v1 simplification - dividend income is realized as a
capital gain on sale rather than taxed in the year received (a future enhancement with
dividend-event data can separate the two via DanishTaxLedger.add_dividend). Cash and
positions are held in USD (the trading currency); equity and tax are reported in DKK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

from .costs import CostModel
from .tax import DanishTaxLedger


class LookaheadError(Exception):
    """A strategy tried to read data at or beyond a date it must not see yet."""


@dataclass(frozen=True)
class TargetPosition:
    """Desired end-state for one symbol. Long-only in v1 (weight in [0, 1])."""

    symbol: str
    weight: float
    stop_price: float | None = None
    reason: str = ""


@dataclass
class Portfolio:
    cash_usd: float
    shares: dict[str, float] = field(default_factory=dict)

    def held(self, symbol: str) -> float:
        return self.shares.get(symbol, 0.0)


@dataclass
class Trade:
    date: pd.Timestamp
    symbol: str
    side: int  # +1 buy, -1 sell
    shares: float
    price_usd: float  # executed price incl. spread/slippage
    commission_usd: float
    reason: str


@dataclass
class BacktestResult:
    equity_dkk: pd.Series  # after-tax equity, indexed by session date
    trades: list[Trade]
    tax_by_year: dict[int, float]
    final_equity_dkk: float


def adjusted_ohlc(df: pd.DataFrame) -> pd.DataFrame:
    """Total-return OHLC: raw open/high/low scaled by the per-day adj_close/close ratio."""
    ratio = df["adj_close"] / df["close"]
    return pd.DataFrame(
        {
            "open": df["open"] * ratio,
            "high": df["high"] * ratio,
            "low": df["low"] * ratio,
            "close": df["adj_close"],
        },
        index=df.index,
    )


class StrategyContext:
    """What a strategy sees on decision date T. Enforces no lookahead: history is
    sliced at T, and any explicit request for a date after T raises LookaheadError."""

    def __init__(
        self,
        adjusted: dict[str, pd.DataFrame],
        date: pd.Timestamp,
        members: list[str],
        portfolio: Portfolio,
    ) -> None:
        self._adjusted = adjusted
        self.date = pd.Timestamp(date)
        self._members = members
        self.portfolio = portfolio

    def universe(self) -> list[str]:
        return list(self._members)

    def history(self, symbol: str) -> pd.DataFrame | None:
        """Adjusted OHLC up to and including the decision date (never beyond)."""
        df = self._adjusted.get(symbol)
        if df is None:
            return None
        return df.loc[: self.date]

    def close(self, symbol: str) -> float | None:
        """Latest adjusted close on or before the decision date."""
        hist = self.history(symbol)
        if hist is None or len(hist) == 0:
            return None
        return float(hist["close"].iloc[-1])

    def close_on(self, symbol: str, date) -> float:
        d = pd.Timestamp(date)
        if d > self.date:
            raise LookaheadError(
                f"strategy requested {d.date()} after decision date {self.date.date()}"
            )
        df = self._adjusted.get(symbol)
        if df is None or d not in df.index:
            raise KeyError(f"no bar for {symbol!r} on {d.date()}")
        return float(df.loc[d, "close"])


class Strategy(Protocol):
    name: str

    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]: ...


class BacktestEngine:
    def __init__(
        self,
        calendar,
        cost_model: CostModel,
        tax_thresholds: dict[int, float],
        *,
        rate_low: float = 0.27,
        rate_high: float = 0.42,
    ) -> None:
        self._calendar = calendar
        self._costs = cost_model
        self._tax_thresholds = tax_thresholds
        self._rate_low = rate_low
        self._rate_high = rate_high

    def run(
        self,
        strategy: Strategy,
        panel: dict[str, pd.DataFrame],
        members_fn,
        start,
        end,
        initial_dkk: float,
        usd_dkk,
    ) -> BacktestResult:
        sessions = self._calendar.sessions(start, end)
        if len(sessions) < 2:
            raise ValueError("backtest needs at least two sessions")
        adjusted = {sym: adjusted_ohlc(df) for sym, df in panel.items()}
        fx = _fx_lookup(usd_dkk)
        tax = DanishTaxLedger(self._tax_thresholds, self._rate_low, self._rate_high)

        # Fund: pay the FX conversion fee once, convert the rest to USD.
        funded_dkk = initial_dkk - self._costs.fx_fee(initial_dkk)
        portfolio = Portfolio(cash_usd=funded_dkk / fx(sessions[0]))

        trades: list[Trade] = []
        # First point is the GROSS committed capital, so the funding FX fee (and any
        # day-0 cost) shows as a return drag rather than being silently rebased away.
        equity: dict[pd.Timestamp, float] = {sessions[0]: float(initial_dkk)}
        tax_by_year: dict[int, float] = {}

        for i in range(len(sessions) - 1):
            decision_day, fill_day = sessions[i], sessions[i + 1]

            members = list(members_fn(decision_day))
            ctx = StrategyContext(adjusted, decision_day, members, portfolio)
            member_set = set(members)
            # Enforce the point-in-time universe (survivorship safety): only current
            # members may be targeted. A target outside it is dropped, so a held symbol
            # that has left the index is exited on the rebalance, never freshly entered.
            targets = {
                t.symbol: t for t in strategy.target_positions(ctx) if t.symbol in member_set
            }

            # Settle the ending year BEFORE sizing/filling the new year's orders, so a
            # prior-year tax liability is not left available as cash for them.
            if fill_day.year != decision_day.year:
                tax_by_year[decision_day.year] = self._settle_year(
                    decision_day.year, portfolio, tax, fx, fill_day, adjusted, trades
                )

            # The stop active on the fill day is the CURRENT decision's stop (placed at
            # the prior close; the strategy re-decides every session). Gap-through-open
            # exits fire before the open orders, on positions held into the session.
            current_stops = {
                sym: t.stop_price for sym, t in targets.items() if t.stop_price is not None
            }
            stopped = self._apply_gap_stops(
                portfolio, current_stops, adjusted, fill_day, fx, tax, trades
            )
            # A gap-stopped symbol is NOT re-entered on the same open; re-entry needs a
            # fresh decision on a later session.
            active = {sym: t for sym, t in targets.items() if sym not in stopped}

            equity_usd = self._equity_usd(portfolio, adjusted, decision_day)
            self._rebalance(portfolio, active, adjusted, fill_day, equity_usd, fx, tax, trades)

            # Intraday touches (open above the stop, low below) fire AFTER the
            # market-on-open orders execute.
            self._apply_intraday_stops(
                portfolio, current_stops, adjusted, fill_day, fx, tax, trades
            )

            equity[fill_day] = self._mark_dkk(portfolio, adjusted, fill_day, fx)

        # Settle the final (partial) year so the reported equity is after-tax.
        final_year = sessions[-1].year
        tax_by_year[final_year] = self._settle_year(
            final_year, portfolio, tax, fx, sessions[-1], adjusted, trades
        )
        equity[sessions[-1]] = self._mark_dkk(portfolio, adjusted, sessions[-1], fx)

        return BacktestResult(
            equity_dkk=pd.Series(equity).sort_index(),
            trades=trades,
            tax_by_year=tax_by_year,
            final_equity_dkk=equity[sessions[-1]],
        )

    # -- internals ---------------------------------------------------------------

    def _price(self, adjusted, symbol, day, field_name) -> float | None:
        df = adjusted.get(symbol)
        if df is None or day not in df.index:
            return None
        return float(df.loc[day, field_name])

    def _mark_price(self, adjusted, symbol, day) -> float:
        """Adjusted close as of `day` (last available on or before it), so a held
        position is valued at its most recent price - never silently at zero when a
        bar is missing (e.g. a delisting tail). Fails loud if there is no price at all
        up to `day` (which cannot happen for a symbol we actually bought)."""
        df = adjusted.get(symbol)
        if df is not None:
            prior = df["close"].loc[: pd.Timestamp(day)]
            if len(prior):
                return float(prior.iloc[-1])
        raise ValueError(f"cannot price held position {symbol!r} as of {pd.Timestamp(day).date()}")

    def _equity_usd(self, portfolio, adjusted, day) -> float:
        total = portfolio.cash_usd
        for symbol, shares in portfolio.shares.items():
            total += shares * self._mark_price(adjusted, symbol, day)
        return total

    def _mark_dkk(self, portfolio, adjusted, day, fx) -> float:
        return self._equity_usd(portfolio, adjusted, day) * fx(day)

    def _buy(self, portfolio, symbol, shares, open_price, day, fx, tax, trades, reason) -> None:
        price = self._costs.fill_price(open_price, side=1)
        commission = self._costs.commission(shares * price)
        portfolio.cash_usd -= shares * price + commission
        portfolio.shares[symbol] = portfolio.held(symbol) + shares
        # Commission raises the per-share DKK cost basis (part of the acquisition cost).
        cost_per_share_dkk = (shares * price + commission) / shares * fx(day)
        tax.buy(symbol, shares, cost_per_share_dkk)
        trades.append(Trade(day, symbol, 1, shares, price, commission, reason))

    def _sell(self, portfolio, symbol, shares, exec_price, day, fx, tax, trades, reason) -> None:
        commission = self._costs.commission(shares * exec_price)
        portfolio.cash_usd += shares * exec_price - commission
        portfolio.shares[symbol] = portfolio.held(symbol) - shares
        if portfolio.shares[symbol] <= 1e-9:
            portfolio.shares.pop(symbol, None)
        # Commission reduces the per-share DKK proceeds.
        proceeds_per_share_dkk = (shares * exec_price - commission) / shares * fx(day)
        tax.sell(symbol, shares, proceeds_per_share_dkk, day.year)
        trades.append(Trade(day, symbol, -1, shares, exec_price, commission, reason))

    def _apply_gap_stops(self, portfolio, stops, adjusted, day, fx, tax, trades) -> set[str]:
        """Positions that gap through their stop at the open fill at the open. Returns
        the symbols stopped out (so they aren't re-entered on the same open)."""
        stopped: set[str] = set()
        for symbol in list(portfolio.shares):
            stop = stops.get(symbol)
            if stop is None:
                continue
            day_open = self._price(adjusted, symbol, day, "open")
            if day_open is not None and day_open <= stop:
                exec_price = self._costs.fill_price(day_open, side=-1)
                shares = portfolio.held(symbol)
                self._sell(portfolio, symbol, shares, exec_price, day, fx, tax, trades, "stop-gap")
                stopped.add(symbol)
        return stopped

    def _apply_intraday_stops(self, portfolio, stops, adjusted, day, fx, tax, trades) -> None:
        """Positions that open above the stop but touch it intraday fill at the stop
        (after the same-session market-on-open orders)."""
        for symbol in list(portfolio.shares):
            stop = stops.get(symbol)
            if stop is None:
                continue
            day_open = self._price(adjusted, symbol, day, "open")
            day_low = self._price(adjusted, symbol, day, "low")
            if day_open is None or day_low is None:
                continue
            if day_open > stop and day_low <= stop:
                exec_price = self._costs.fill_price(stop, side=-1)
                shares = portfolio.held(symbol)
                self._sell(portfolio, symbol, shares, exec_price, day, fx, tax, trades, "stop")

    def _rebalance(self, portfolio, targets, adjusted, day, equity_usd, fx, tax, trades) -> None:
        current = dict(portfolio.shares)
        desired: dict[str, int] = {}
        for symbol, target in targets.items():
            open_price = self._price(adjusted, symbol, day, "open")
            if open_price and open_price > 0:
                desired[symbol] = int((equity_usd * target.weight) / open_price)

        # Sells first (free up cash), then buys.
        for symbol in list(current):
            delta = desired.get(symbol, 0) - current[symbol]
            open_price = self._price(adjusted, symbol, day, "open")
            if delta < 0 and open_price is not None:
                exec_price = self._costs.fill_price(open_price, side=-1)
                self._sell(portfolio, symbol, -delta, exec_price, day, fx, tax, trades, "rebalance")
        for symbol, want in desired.items():
            open_price = self._price(adjusted, symbol, day, "open")
            if open_price is None:
                continue
            delta = want - portfolio.held(symbol)
            if delta > 0:
                # Cap by what the cash can actually afford at the fill price + commission
                # (sizing used the raw open), so an all-in target can't overdraw / lever.
                delta = min(delta, self._max_affordable(portfolio.cash_usd, open_price))
            if delta > 0:
                reason = targets[symbol].reason or "rebalance"
                self._buy(portfolio, symbol, delta, open_price, day, fx, tax, trades, reason)

    def _max_affordable(self, cash_usd: float, open_price: float) -> int:
        """Largest whole-share buy whose fill price + commission fits in cash."""
        fill = self._costs.fill_price(open_price, side=1)
        if fill <= 0:
            return 0
        shares = int(cash_usd / fill)
        while shares > 0 and shares * fill + self._costs.commission(shares * fill) > cash_usd:
            shares -= 1
        return shares

    def _raise_cash(self, portfolio, needed_usd, adjusted, day, fx, tax, trades) -> None:
        """Liquidate positions (at the day's open) until cash covers needed_usd, so
        paying tax can't push the book into leverage. Accounts for sell commission by
        re-checking after each sale and selling more if still short."""
        for symbol in list(portfolio.shares):
            open_price = self._price(adjusted, symbol, day, "open")
            if open_price is None or open_price <= 0:
                continue
            exec_price = self._costs.fill_price(open_price, side=-1)
            while portfolio.cash_usd < needed_usd and portfolio.held(symbol) > 0:
                shortfall = needed_usd - portfolio.cash_usd
                shares = min(portfolio.held(symbol), max(1, int(shortfall / exec_price) + 1))
                before = portfolio.cash_usd
                self._sell(portfolio, symbol, shares, exec_price, day, fx, tax, trades, "tax-raise")
                if portfolio.cash_usd <= before:  # a sale that nets nothing (all commission)
                    break
            if portfolio.cash_usd >= needed_usd:
                return

    def _settle_year(self, year, portfolio, tax, fx, day, adjusted, trades) -> float:
        tax_dkk = tax.close_year(year)  # raises on an unconfigured year (fail loud)
        if not tax_dkk:
            return 0.0
        # Paying tax converts USD->DKK, which also incurs the FX conversion fee.
        needed_usd = (tax_dkk + self._costs.fx_fee(tax_dkk)) / fx(day)
        # Raise cash only when the sale lands in a still-open year (a mid-backtest
        # rollover: day.year > the settled year). At final settlement day.year == year,
        # which is now closed, so accrue the tax against equity instead of selling.
        if portfolio.cash_usd < needed_usd and day.year != year:
            self._raise_cash(portfolio, needed_usd, adjusted, day, fx, tax, trades)
        portfolio.cash_usd -= needed_usd
        return tax_dkk


def _fx_lookup(usd_dkk):
    if callable(usd_dkk):
        return usd_dkk
    if isinstance(usd_dkk, (int, float)):
        return lambda _day: float(usd_dkk)
    series = pd.Series(usd_dkk).sort_index()

    def _lookup(day):
        d = pd.Timestamp(day)
        prior = series.loc[:d]
        if len(prior) == 0:
            raise KeyError(f"no USD/DKK rate on or before {d.date()}")
        return float(prior.iloc[-1])

    return _lookup
