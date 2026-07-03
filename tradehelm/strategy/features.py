"""Precomputed causal indicator columns (Fable review F1).

The candidates need the same handful of rolling indicators on every decision day.
Recomputing them over a growing history prefix each day is O(days^2) per symbol and
makes the full research study take ~1,200 h. Because every indicator here is causal
(the value at row k depends only on rows <= k - locked by
test_indicators_have_no_lookahead), we compute each column ONCE over the full adjusted
frame and let the strategy read the value as of its decision date. That is identical to
recomputing on the prefix, but O(1) per lookup instead of O(days).

`resolve_feature` maps a feature name to its builder, including parametric names
(`highest_20`, `lowest_10_prev`) so a lookback outside the precomputed default set
still resolves (computed on demand, still causal). `build_features` precomputes the
default set covering the STRATEGY_SPEC grids.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import pandas as pd

from . import indicators as ind

# Fixed (non-parametric) features, keyed by the name a strategy asks for.
_FIXED: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "sma5": lambda f: ind.sma(f["close"], 5),
    "sma200": lambda f: ind.sma(f["close"], 200),
    "rsi2": lambda f: ind.rsi(f["close"], 2),
    "atr14": lambda f: ind.atr(f, 14),
    "ret100": lambda f: ind.trailing_return(f["close"], 100),
    "ret126s5": lambda f: ind.trailing_return(f["close"], 126, skip=5),
    "ret252s5": lambda f: ind.trailing_return(f["close"], 252, skip=5),
    "mdv20": lambda f: ind.median_dollar_volume(f, 20),
}

_SMA = re.compile(r"^sma(\d+)$")
_HIGHEST = re.compile(r"^highest_(\d+)$")
_LOWEST_PREV = re.compile(r"^lowest_(\d+)_prev$")


def resolve_feature(name: str) -> Callable[[pd.DataFrame], pd.Series]:
    """Return the builder for a feature name (fixed or parametric)."""
    if name in _FIXED:
        return _FIXED[name]
    m = _SMA.match(name)
    if m:
        n = int(m.group(1))
        return lambda f: ind.sma(f["close"], n)
    m = _HIGHEST.match(name)
    if m:
        n = int(m.group(1))
        return lambda f: ind.highest_close(f["close"], n)
    m = _LOWEST_PREV.match(name)
    if m:
        n = int(m.group(1))
        return lambda f: ind.lowest_close(f["close"], n).shift(1)
    raise KeyError(f"unknown feature {name!r}")


# The set precomputed by default - every column the candidate grids in
# STRATEGY_SPEC.md reference (entry_lookback in {20,55}, exit_lookback in {10,20}).
DEFAULT_FEATURES: tuple[str, ...] = (
    *_FIXED,
    "highest_20",
    "highest_55",
    "lowest_10_prev",
    "lowest_20_prev",
)


def build_features(
    adjusted: dict[str, pd.DataFrame], names: tuple[str, ...] = DEFAULT_FEATURES
) -> dict[str, pd.DataFrame]:
    """Precompute the named feature columns for every symbol's adjusted frame.

    Each result frame shares its symbol's index, so a positional lookup at the
    decision date reads the adjusted bar and its features at the same row."""
    out: dict[str, pd.DataFrame] = {}
    for symbol, frame in adjusted.items():
        out[symbol] = pd.DataFrame(
            {name: resolve_feature(name)(frame) for name in names}, index=frame.index
        )
    return out
