"""Backtest metrics tests."""

import pandas as pd
import pytest

from tradehelm.backtest import metrics


def _equity(values, start="2020-01-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_total_return():
    assert metrics.total_return(_equity([100, 110, 121])) == pytest.approx(0.21)


def test_max_drawdown():
    # peak 120 -> trough 90 = -25%
    assert metrics.max_drawdown(_equity([100, 120, 90, 150])) == pytest.approx(-0.25)


def test_cagr_one_year_is_the_period_return():
    idx = pd.to_datetime(["2020-01-01", "2021-01-01"])
    equity = pd.Series([100.0, 110.0], index=idx)
    assert metrics.cagr(equity) == pytest.approx(0.10, rel=1e-2)


def test_sharpe_positive_for_uptrend_zero_for_flat():
    up = _equity([100 * 1.001**i for i in range(60)])
    assert metrics.sharpe(up) > 0
    assert metrics.sharpe(_equity([100.0] * 10)) == 0.0


def test_probabilistic_sharpe_high_for_strong_uptrend():
    up = _equity([100 * 1.002**i for i in range(120)])
    assert metrics.probabilistic_sharpe_ratio(up) > 0.9


def test_summary_has_expected_keys():
    s = metrics.summary(_equity([100, 101, 102, 103]))
    assert set(s) == {"total_return", "cagr", "annualized_volatility", "max_drawdown", "sharpe"}
