"""Deterministic lightweight feature helpers for intraday strategies."""
from __future__ import annotations

from tradehelm.trading_engine.types import Bar


def session_bar_count(history: list[Bar]) -> int:
    return len(history)


def opening_range(history: list[Bar], window_bars: int) -> tuple[float, float] | None:
    if len(history) < window_bars:
        return None
    opening = history[:window_bars]
    return max(b.high for b in opening), min(b.low for b in opening)


def cumulative_vwap(history: list[Bar]) -> float | None:
    if not history:
        return None
    volume_sum = sum(max(b.volume, 0.0) for b in history)
    if volume_sum <= 0:
        return None
    pv_sum = sum(b.close * max(b.volume, 0.0) for b in history)
    return pv_sum / volume_sum


def rolling_high(history: list[Bar], bars: int) -> float | None:
    if len(history) < bars:
        return None
    return max(b.high for b in history[-bars:])


def rolling_low(history: list[Bar], bars: int) -> float | None:
    if len(history) < bars:
        return None
    return min(b.low for b in history[-bars:])
