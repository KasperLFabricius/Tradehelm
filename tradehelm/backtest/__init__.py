"""Backtester (event-driven daily loop, CostModel, Danish tax model, metrics,
walk-forward). See docs/ARCHITECTURE.md section 4 and docs/COSTS_AND_TAX.md.
"""

from __future__ import annotations

from . import metrics
from .costs import CostModel
from .engine import (
    BacktestEngine,
    BacktestResult,
    LookaheadError,
    Portfolio,
    StrategyContext,
    TargetPosition,
    Trade,
    adjusted_ohlc,
)
from .tax import DanishTaxLedger, progressive_tax
from .walkforward import Window, holdout_range, run_walk_forward, walk_forward_windows

__all__ = [
    "BacktestEngine",
    "BacktestResult",
    "CostModel",
    "DanishTaxLedger",
    "LookaheadError",
    "Portfolio",
    "StrategyContext",
    "TargetPosition",
    "Trade",
    "Window",
    "adjusted_ohlc",
    "holdout_range",
    "metrics",
    "progressive_tax",
    "run_walk_forward",
    "walk_forward_windows",
]
