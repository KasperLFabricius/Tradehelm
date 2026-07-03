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


def holdout_range(end, holdout_years: int = 2) -> tuple[pd.Timestamp, pd.Timestamp]:
    """The final untouched holdout span [end - holdout_years, end] (Gate 3G)."""
    end = pd.Timestamp(end)
    return end - pd.DateOffset(years=holdout_years), end


def _purged_start(train_end: pd.Timestamp, purge_sessions: int, calendar) -> pd.Timestamp:
    """Test-window start, leaving purge_sessions TRADING sessions between it and
    train_end (Lopez de Prado embargo)."""
    window_end = train_end + pd.Timedelta(days=purge_sessions * 4 + 30)
    after = calendar.sessions(train_end + pd.Timedelta(days=1), window_end)  # sessions > train_end
    if len(after) <= purge_sessions:  # near the end of data
        return window_end
    return after[purge_sessions]  # purge_sessions sessions purged; test starts on the next


def walk_forward_windows(
    start,
    end,
    train_years: int = 3,
    test_years: int = 1,
    purge_sessions: int = 5,
    holdout_years: int = 2,
    calendar=None,
) -> list[Window]:
    """Rolling train/test windows over [start, end - holdout_years].

    The purge between train and test is purge_sessions TRADING days, so a `calendar` is
    REQUIRED (calendar days would under-purge near weekends/holidays). The final
    holdout_years are RESERVED (never appear in a test window) so the Gate 3G holdout
    stays untouched even when callers pass the full data range as end; holdout_years=0
    uses all data.
    """
    if train_years <= 0 or test_years <= 0:
        raise ValueError("train_years and test_years must be positive")
    if holdout_years < 0:
        raise ValueError("holdout_years must be non-negative")
    if calendar is None:
        raise ValueError("a trading calendar is required for the trading-day purge")
    start = pd.Timestamp(start)
    walk_end = pd.Timestamp(end) - pd.DateOffset(years=holdout_years)

    # First collect the (train, test_start) schedule.
    schedule: list[tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]] = []
    train_start = start
    while True:
        train_end = train_start + pd.DateOffset(years=train_years)
        test_start = _purged_start(train_end, purge_sessions, calendar)
        if test_start >= walk_end:
            break
        schedule.append((train_start, train_end, test_start))
        train_start = train_start + pd.DateOffset(years=test_years)

    # test_end is INCLUSIVE for BacktestEngine.run. End each window the day before the
    # next window starts (and before the holdout at walk_end), capped to test_years, so
    # windows tile without sharing a boundary session and never touch the holdout.
    one_day = pd.Timedelta(days=1)
    windows: list[Window] = []
    for idx, (tr_start, tr_end, test_start) in enumerate(schedule):
        next_start = schedule[idx + 1][2] if idx + 1 < len(schedule) else walk_end
        test_end = min(next_start, walk_end, test_start + pd.DateOffset(years=test_years)) - one_day
        windows.append(Window(tr_start, tr_end, test_start, test_end))
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
