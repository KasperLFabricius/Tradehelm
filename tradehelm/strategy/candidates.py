"""The three deterministic v1 strategy candidates (docs/STRATEGY_SPEC.md).

Each is a pure, backtestable rule set: given the point-in-time universe and price
history up to the decision date, it returns the desired end-state positions. Stops
are entry-relative and tracked in a PositionBook. No LLM/API judgment (CLAUDE.md
rule 1); no data beyond the decision date is read.

Sizing is delegated to the engine via TargetPosition.weight; holds use weight=None
so a position is not resized by daily price drift. All long-only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

import pandas as pd

from ..backtest.engine import StrategyContext, TargetPosition
from . import indicators as ind
from .base import (
    REGIME_SMA,
    EntrySignal,
    _CandidateBase,
    _isnan,
    _latest,
    allocate_new_entries,
    current_exposure_frac,
    is_week_end_session,
    liquidity_ok,
    regime_ok,
)


@dataclass
class CandidateA(_CandidateBase):
    """Trend-filtered pullback (short-term mean reversion), highest turnover.

    Entry: instrument and SPY above SMA(200), RSI(2) < `rsi_entry`, close < SMA(5).
    Exit: close > SMA(5), or RSI(2) > 70, or `max_hold` sessions elapsed. Protective
    stop: entry - `stop_atr` * ATR(14). Ranked by lowest RSI(2) first.
    """

    name: ClassVar[str] = "candidate_a"
    rsi_entry: float = 10.0
    stop_atr: float = 3.0
    max_hold: int = 5

    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]:
        self.book.sync(ctx)
        held = {s for s, q in ctx.portfolio.shares.items() if q > 0}
        targets: list[TargetPosition] = []
        keep: set[str] = set()

        for symbol in held:
            hist = ctx.history(symbol)
            if hist is None or len(hist) < 5:
                continue  # cannot evaluate the exit -> let it be sold (defensive)
            close = hist["close"]
            c = _latest(close)
            sma5 = _latest(ind.sma(close, 5))
            r2 = _latest(ind.rsi(close, 2))
            if _isnan(c) or _isnan(sma5) or _isnan(r2):
                continue
            days = self.book.days_held(symbol, hist)
            if c > sma5 or r2 > 70.0 or days >= self.max_hold:
                continue  # exit signal -> omit, engine sells at next open
            lot = self.book.lot(symbol)
            stop = None if lot is None else lot.entry_price - self.stop_atr * lot.entry_atr
            targets.append(TargetPosition(symbol, weight=None, stop_price=stop, reason="A-hold"))
            keep.add(symbol)

        if regime_ok(ctx, self.regime_symbol, REGIME_SMA):
            ranked = self._entry_signals(ctx, held)
            exposure = current_exposure_frac(ctx, keep)
            for symbol, weight, stop in allocate_new_entries(
                ranked, self.risk, len(keep), exposure
            ):
                targets.append(
                    TargetPosition(symbol, weight=weight, stop_price=stop, reason="A-entry")
                )
        return targets

    def _entry_signals(self, ctx: StrategyContext, held: set[str]) -> list[EntrySignal]:
        out: list[EntrySignal] = []
        for symbol in ctx.universe():
            if symbol in held:
                continue
            hist = ctx.history(symbol)
            if hist is None or len(hist) < REGIME_SMA:
                continue
            if not liquidity_ok(hist, self.min_price, self.min_dollar_volume):
                continue
            close = hist["close"]
            c = _latest(close)
            sma200 = _latest(ind.sma(close, 200))
            sma5 = _latest(ind.sma(close, 5))
            r2 = _latest(ind.rsi(close, 2))
            a14 = _latest(ind.atr(hist, 14))
            if any(_isnan(x) for x in (c, sma200, sma5, r2, a14)):
                continue
            if c <= sma200 or r2 >= self.rsi_entry or c >= sma5:
                continue
            stop = c - self.stop_atr * a14
            if stop <= 0 or stop >= c:
                continue
            out.append(EntrySignal(symbol, entry_ref=c, stop=stop, rank_key=r2))
        out.sort(key=lambda s: s.rank_key)  # lowest RSI(2) first
        return out


@dataclass
class CandidateB(_CandidateBase):
    """Breakout continuation (momentum), medium turnover.

    Entry: SPY above SMA(200) and close = highest close of the last `entry_lookback`
    sessions. Exit: close < lowest close of the prior `exit_lookback` sessions, or a
    trailing stop at `stop_atr` * ATR(14) below the highest close since entry. Ranked
    by highest 100-day return first.
    """

    name: ClassVar[str] = "candidate_b"
    entry_lookback: int = 20
    exit_lookback: int = 10
    stop_atr: float = 3.0

    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]:
        self.book.sync(ctx)
        held = {s for s, q in ctx.portfolio.shares.items() if q > 0}
        targets: list[TargetPosition] = []
        keep: set[str] = set()

        for symbol in held:
            hist = ctx.history(symbol)
            if hist is None or len(hist) < self.exit_lookback + 1:
                continue
            close = hist["close"]
            c = _latest(close)
            prior_low = _latest(ind.lowest_close(close, self.exit_lookback).shift(1))
            a14 = _latest(ind.atr(hist, 14))
            if _isnan(c) or _isnan(prior_low):
                continue
            if c < prior_low:
                continue  # channel breakdown -> exit
            lot = self.book.lot(symbol)
            if lot is not None and not _isnan(a14):
                raw = lot.high_since - self.stop_atr * a14
                stop = self.book.ratchet_stop(symbol, raw)  # trailing, ratchets up only
            else:
                stop = None
            targets.append(TargetPosition(symbol, weight=None, stop_price=stop, reason="B-hold"))
            keep.add(symbol)

        if regime_ok(ctx, self.regime_symbol, REGIME_SMA):
            ranked = self._entry_signals(ctx, held)
            exposure = current_exposure_frac(ctx, keep)
            for symbol, weight, stop in allocate_new_entries(
                ranked, self.risk, len(keep), exposure
            ):
                targets.append(
                    TargetPosition(symbol, weight=weight, stop_price=stop, reason="B-entry")
                )
        return targets

    def _entry_signals(self, ctx: StrategyContext, held: set[str]) -> list[EntrySignal]:
        out: list[EntrySignal] = []
        for symbol in ctx.universe():
            if symbol in held:
                continue
            hist = ctx.history(symbol)
            if hist is None or len(hist) < max(self.entry_lookback, 100, 14):
                continue
            if not liquidity_ok(hist, self.min_price, self.min_dollar_volume):
                continue
            close = hist["close"]
            c = _latest(close)
            hh = _latest(ind.highest_close(close, self.entry_lookback))
            a14 = _latest(ind.atr(hist, 14))
            mom = _latest(ind.trailing_return(close, 100))
            if any(_isnan(x) for x in (c, hh, a14, mom)):
                continue
            if c < hh:  # not a new `entry_lookback`-session high (hh includes today)
                continue
            stop = c - self.stop_atr * a14
            if stop <= 0 or stop >= c:
                continue
            out.append(EntrySignal(symbol, entry_ref=c, stop=stop, rank_key=-mom))
        out.sort(key=lambda s: s.rank_key)  # highest 100-day return first
        return out


@dataclass
class CandidateC(_CandidateBase):
    """Weekly cross-sectional momentum rotation, lowest turnover (tax-friendliest).

    Decides only on the last session of each week. Score = 0.5*r(126) + 0.5*r(252),
    each skipping the most recent 5 sessions. Holds the top `n_hold`; sells a holding
    only when it drops out of the top `n_hold * buffer`. SPY below SMA(200) -> cash.
    Protective stop: 20% below entry (catastrophe only).
    """

    name: ClassVar[str] = "candidate_c"
    n_hold: int = 5
    buffer: float = 1.5
    calendar: object | None = None  # market calendar; falls back to a Friday heuristic
    catastrophe_frac: float = 0.20

    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]:
        self.book.sync(ctx)
        held = {s for s, q in ctx.portfolio.shares.items() if q > 0}

        if not self._is_decision_day(ctx.date):
            # Between weekly decisions: hold every position as-is with its resting
            # catastrophe stop; take no new action.
            return self._hold_all(held)

        if not regime_ok(ctx, self.regime_symbol, REGIME_SMA):
            return []  # risk-off -> move fully to cash

        scores = self._scores(ctx)
        if not scores:
            return self._hold_all(held)
        ranked = [sym for sym, _ in scores]  # already sorted best-first
        buffer_n = max(self.n_hold, math.ceil(self.n_hold * self.buffer))
        buffer_set = set(ranked[:buffer_n])

        targets: list[TargetPosition] = []
        keep: set[str] = set()
        for symbol in held:
            if symbol in buffer_set:  # still strong enough -> hold (reduce churn)
                targets.append(
                    TargetPosition(
                        symbol, weight=None, stop_price=self._catastrophe_stop(symbol),
                        reason="C-hold",
                    )
                )
                keep.add(symbol)
            # else: dropped out of the buffer band -> omit, engine sells

        slots = self.n_hold - len(keep)
        if slots > 0:
            new_signals: list[EntrySignal] = []
            for symbol in ranked:
                if len(new_signals) >= slots:
                    break
                if symbol in keep or symbol in held:
                    continue
                c = ctx.close(symbol)
                if c is None or c <= 0:
                    continue
                stop = c * (1.0 - self.catastrophe_frac)
                new_signals.append(EntrySignal(symbol, entry_ref=c, stop=stop, rank_key=0.0))
            exposure = current_exposure_frac(ctx, keep)
            for symbol, weight, stop in allocate_new_entries(
                new_signals, self.risk, len(keep), exposure
            ):
                targets.append(
                    TargetPosition(symbol, weight=weight, stop_price=stop, reason="C-entry")
                )
        return targets

    def _hold_all(self, held: set[str]) -> list[TargetPosition]:
        """Re-emit every current holding as a HOLD with its catastrophe stop."""
        return [
            TargetPosition(s, weight=None, stop_price=self._catastrophe_stop(s), reason="C-hold")
            for s in held
        ]

    def _catastrophe_stop(self, symbol: str) -> float | None:
        lot = self.book.lot(symbol)
        if lot is None:
            return None
        return lot.entry_price * (1.0 - self.catastrophe_frac)

    def _is_decision_day(self, day) -> bool:
        if self.calendar is not None:
            return is_week_end_session(self.calendar, day)
        return pd.Timestamp(day).weekday() == 4  # heuristic fallback: Friday

    def _scores(self, ctx: StrategyContext) -> list[tuple[str, float]]:
        scored: list[tuple[str, float]] = []
        for symbol in ctx.universe():
            hist = ctx.history(symbol)
            if hist is None or len(hist) < 252 + 5:
                continue
            if not liquidity_ok(hist, self.min_price, self.min_dollar_volume):
                continue
            close = hist["close"]
            r126 = _latest(ind.trailing_return(close, 126, skip=5))
            r252 = _latest(ind.trailing_return(close, 252, skip=5))
            if _isnan(r126) or _isnan(r252):
                continue
            scored.append((symbol, 0.5 * r126 + 0.5 * r252))
        scored.sort(key=lambda x: x[1], reverse=True)  # highest blended momentum first
        return scored


__all__ = ["CandidateA", "CandidateB", "CandidateC"]
