"""Walk-forward validation windows and runner (docs/PLAN.md Phase 2/3).

Rolling split: train_years in-sample, test_years out-of-sample, rolled forward by
test_years, with a purge gap between train and test so no sample straddles the
boundary (Lopez de Prado). Parameter selection (fit_fn) belongs to the research in
Phase 3; here we only generate the schedule and run a fitted strategy per test window.
The final untouched holdout is run separately via an explicit flag in the scripts.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from .costs import CostModel
from .engine import BacktestEngine, BacktestResult, Strategy


@dataclass(frozen=True)
class Window:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def walk_forward_windows(
    start,
    end,
    train_years: int = 3,
    test_years: int = 1,
    purge_days: int = 5,
) -> list[Window]:
    if train_years <= 0 or test_years <= 0:
        raise ValueError("train_years and test_years must be positive")
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    windows: list[Window] = []
    train_start = start
    while True:
        train_end = train_start + pd.DateOffset(years=train_years)
        test_start = train_end + pd.Timedelta(days=purge_days)
        if test_start >= end:
            break
        test_end = min(test_start + pd.DateOffset(years=test_years), end)
        windows.append(Window(train_start, train_end, test_start, test_end))
        train_start = train_start + pd.DateOffset(years=test_years)
    return windows


def run_walk_forward(
    windows: list[Window],
    fit_fn: Callable[[pd.Timestamp, pd.Timestamp], Strategy],
    panel: dict[str, pd.DataFrame],
    members_fn,
    calendar,
    cost_model: CostModel,
    tax_thresholds: dict[int, float],
    initial_dkk: float,
    usd_dkk,
) -> list[tuple[Window, BacktestResult]]:
    """Fit a strategy on each window's train span, then backtest it out-of-sample
    on the test span. fit_fn(train_start, train_end) must select parameters using
    ONLY the training range."""
    results: list[tuple[Window, BacktestResult]] = []
    for window in windows:
        strategy = fit_fn(window.train_start, window.train_end)
        engine = BacktestEngine(calendar, cost_model, tax_thresholds)
        result = engine.run(
            strategy, panel, members_fn, window.test_start, window.test_end, initial_dkk, usd_dkk
        )
        results.append((window, result))
    return results
