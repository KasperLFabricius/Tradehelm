"""Backtest performance metrics on an after-tax equity curve (a pandas Series
indexed by session date).

The probabilistic Sharpe ratio is here, plus the DEFLATED Sharpe (Phase 3), which
discounts the observed Sharpe for the number of strategy trials tried - it needs
the true trial count from research/trials.csv.
"""

from __future__ import annotations

import math
from statistics import NormalDist

import pandas as pd

TRADING_DAYS = 252
_EULER_MASCHERONI = 0.5772156649015329


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    return NormalDist().inv_cdf(p)


def daily_returns(equity: pd.Series) -> pd.Series:
    return equity.sort_index().pct_change().dropna()


def total_return(equity: pd.Series) -> float:
    equity = equity.sort_index()
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def cagr(equity: pd.Series) -> float:
    equity = equity.sort_index()
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0)


def max_drawdown(equity: pd.Series) -> float:
    """Most negative peak-to-trough drawdown (<= 0)."""
    equity = equity.sort_index()
    drawdown = equity / equity.cummax() - 1.0
    return float(drawdown.min())


def annualized_volatility(equity: pd.Series, periods_per_year: int = TRADING_DAYS) -> float:
    rets = daily_returns(equity)
    if len(rets) < 2:
        return 0.0
    return float(rets.std(ddof=1) * math.sqrt(periods_per_year))


def sharpe(
    equity: pd.Series, periods_per_year: int = TRADING_DAYS, risk_free: float = 0.0
) -> float:
    """Annualized Sharpe on daily returns (risk_free is an annual rate)."""
    rets = daily_returns(equity)
    if len(rets) < 2:
        return 0.0
    std = rets.std(ddof=1)
    if std == 0:
        return 0.0
    excess = rets - risk_free / periods_per_year
    return float(excess.mean() / std * math.sqrt(periods_per_year))


def sharpe_per_period(equity: pd.Series) -> float:
    """Per-period (non-annualized) Sharpe = mean/std of daily returns. This is the
    scale the (probabilistic/deflated) Sharpe machinery works in."""
    rets = daily_returns(equity)
    if len(rets) < 2 or rets.std(ddof=1) == 0:
        return 0.0
    return float(rets.mean() / rets.std(ddof=1))


def probabilistic_sharpe_ratio(equity: pd.Series, benchmark_sharpe: float = 0.0) -> float:
    """P(true Sharpe > benchmark), accounting for sample length, skew and kurtosis
    of the return distribution (Bailey & Lopez de Prado). Per-period, not annualized.
    `benchmark_sharpe` is a per-period Sharpe."""
    rets = daily_returns(equity)
    n = len(rets)
    if n < 3 or rets.std(ddof=1) == 0:
        return 0.0
    sr = rets.mean() / rets.std(ddof=1)  # per-period Sharpe
    skew = float(pd.Series(rets).skew())
    kurt = float(pd.Series(rets).kurt()) + 3.0  # pandas kurt is excess; want full
    denom = math.sqrt(max(1e-12, 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr**2))
    return float(_norm_cdf((sr - benchmark_sharpe) * math.sqrt(n - 1) / denom))


def expected_max_sharpe(sr_variance: float, n_trials: int) -> float:
    """Expected maximum per-period Sharpe from `n_trials` independent trials whose
    Sharpes have variance `sr_variance` (Bailey & Lopez de Prado's SR*). This is the
    Sharpe you'd expect to see by luck alone after trying that many configurations."""
    if n_trials < 2 or sr_variance <= 0.0:
        return 0.0
    e = math.e
    gamma = _EULER_MASCHERONI
    return math.sqrt(sr_variance) * (
        (1.0 - gamma) * _norm_ppf(1.0 - 1.0 / n_trials)
        + gamma * _norm_ppf(1.0 - 1.0 / (n_trials * e))
    )


def deflated_sharpe_ratio(equity: pd.Series, sr_variance: float, n_trials: int) -> float:
    """Probabilistic Sharpe against the expected-max Sharpe of `n_trials` trials
    (variance `sr_variance` of their per-period Sharpes). P(true SR > luck).

    A DSR near 1 means the observed Sharpe survives the multiple-testing discount; near
    0 means it is indistinguishable from the best of that many random tries."""
    sr_star = expected_max_sharpe(sr_variance, n_trials)
    return probabilistic_sharpe_ratio(equity, benchmark_sharpe=sr_star)


def summary(equity: pd.Series) -> dict[str, float]:
    return {
        "total_return": total_return(equity),
        "cagr": cagr(equity),
        "annualized_volatility": annualized_volatility(equity),
        "max_drawdown": max_drawdown(equity),
        "sharpe": sharpe(equity),
    }
