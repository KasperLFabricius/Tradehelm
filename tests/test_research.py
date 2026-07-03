"""Tests for the Phase 3b research harness: append-only trials ledger, deflated
Sharpe, walk-forward study (selection + plateau + OOS stitch + benchmark), and the
REPORT.md renderer. All on tiny synthetic panels so CI stays fast and offline."""

import numpy as np
import pandas as pd
import pytest

from tradehelm.backtest import CostModel, metrics
from tradehelm.config import CostConfig
from tradehelm.data import TradingCalendar
from tradehelm.research import (
    CandidateStudy,
    Trial,
    TrialLog,
    build_report,
    doubled_costs,
    params_str,
    run_candidate_study,
    summarize_returns,
)
from tradehelm.strategy import RiskParams

RISK = RiskParams(max_positions=5, per_position_risk_frac=0.01, max_position_notional_frac=0.30)
COSTS = CostConfig(
    commission_rate_us=0.0008,
    min_commission_us=1.0,
    half_spread_bps=2.0,
    slippage_bps=1.0,
    fx_conversion_rate=0.0025,
    custody_fee_annual=0.0,
)


def _panel(sessions):
    n = len(sessions)
    rng = np.random.default_rng(3)

    def frame(drift, vol, start):
        close = pd.Series(start * np.exp(np.cumsum(rng.normal(drift, vol, n))), index=sessions)
        op = close.shift(1).fillna(close.iloc[0])
        hi = np.maximum(op.values, close.values) * (1 + np.abs(rng.normal(0, 0.004, n)))
        lo = np.minimum(op.values, close.values) * (1 - np.abs(rng.normal(0, 0.004, n)))
        return pd.DataFrame(
            {
                "open": op,
                "high": hi,
                "low": lo,
                "close": close,
                "adj_close": close,
                "volume": [4_000_000] * n,
            },
            index=sessions,
        )

    panel = {"SPY": frame(0.0004, 0.008, 100.0)}
    for i in range(6):
        panel[f"N{i}"] = frame(0.0005 + 0.0001 * i, 0.018, 40 + 8 * i)
    return panel


@pytest.fixture(scope="module")
def study_env():
    cal = TradingCalendar()
    sessions = cal.sessions("2016-01-04", "2019-06-30")
    panel = _panel(sessions)
    return cal, panel, [f"N{i}" for i in range(6)], sessions


def _run(env, trials, name="candidate_c", **kw):
    cal, panel, names, _ = env
    return run_candidate_study(
        name,
        panel,
        lambda _d: names,
        cal,
        CostModel(COSTS),
        {y: 60_000.0 for y in range(2016, 2020)},
        "2016-01-04",
        "2019-06-30",
        100_000.0,
        7.0,
        RISK,
        trials,
        train_years=1,
        test_years=1,
        holdout_years=0,
        **kw,
    )


# --------------------------------------------------------------------- trials


def test_trial_log_is_append_only(tmp_path):
    log = TrialLog(tmp_path / "trials.csv")
    assert log.count() == 0
    row = Trial(
        "candidate_a",
        "train",
        0,
        "a=1",
        1.0,
        "27/42",
        "2016-01-01",
        "2017-01-01",
        200,
        0.5,
        0.03,
        0.1,
        -0.2,
        0.15,
        110_000.0,
    )
    log.append(row)
    log.append(row)
    assert log.count() == 2  # appended, not overwritten
    TrialLog(tmp_path / "trials.csv").append(row)  # a fresh handle keeps appending
    assert log.count() == 3
    header = (tmp_path / "trials.csv").read_text(encoding="utf-8").splitlines()[0]
    assert header.split(",")[0] == "candidate" and "sharpe_pp" in header


def test_params_str_is_sorted_and_deterministic():
    assert params_str({"b": 2, "a": 1}) == "a=1;b=2"
    assert params_str({"a": 1, "b": 2}) == "a=1;b=2"


def test_doubled_costs_scales_and_caps():
    d = doubled_costs(COSTS)
    assert d.half_spread_bps == 4.0 and d.slippage_bps == 2.0
    assert d.commission_rate_us == pytest.approx(0.0016)
    capped = doubled_costs(
        CostConfig(
            commission_rate_us=0.7,
            min_commission_us=1.0,
            half_spread_bps=1.0,
            slippage_bps=1.0,
            fx_conversion_rate=0.6,
            custody_fee_annual=0.0,
        )
    )
    assert capped.commission_rate_us == 1.0 and capped.fx_conversion_rate == 1.0  # <= 100%


# ------------------------------------------------------------- deflated Sharpe


def test_expected_max_sharpe_grows_with_trials():
    assert metrics.expected_max_sharpe(0.04, 1) == 0.0  # need >= 2 trials
    assert metrics.expected_max_sharpe(0.0, 100) == 0.0  # need positive variance
    few = metrics.expected_max_sharpe(0.04, 10)
    many = metrics.expected_max_sharpe(0.04, 1000)
    assert 0.0 < few < many  # more trials -> higher luck threshold


def test_deflated_sharpe_discounts_for_trials():
    idx = pd.bdate_range("2018-01-01", periods=400)
    rng = np.random.default_rng(1)
    equity = pd.Series(100_000 * np.cumprod(1 + rng.normal(0.0006, 0.01, 400)), index=idx)
    psr = metrics.probabilistic_sharpe_ratio(equity, 0.0)
    dsr_one = metrics.deflated_sharpe_ratio(equity, 0.05, 1)  # 1 trial -> no deflation
    dsr_many = metrics.deflated_sharpe_ratio(equity, 0.05, 500)
    assert dsr_one == pytest.approx(psr)
    assert 0.0 <= dsr_many <= dsr_one <= 1.0  # many trials cannot raise significance


def test_summarize_returns_basic():
    idx = pd.bdate_range("2020-01-01", periods=4)
    r = pd.Series([0.1, -0.05, 0.02, 0.03], index=idx)
    out = summarize_returns(r)
    assert out["total_return"] == pytest.approx((1.1 * 0.95 * 1.02 * 1.03) - 1.0)
    assert out["n_obs"] == 4
    assert out["max_drawdown"] <= 0.0


# ------------------------------------------------------------------- study


def test_run_candidate_study_selects_logs_and_plateaus(study_env, tmp_path):
    trials = TrialLog(tmp_path / "trials.csv")
    study = _run(study_env, trials)
    grid = 4  # candidate_c: n_hold{3,5} x buffer{1.5,2.0}
    w = len(study.windows)
    assert w >= 1
    # every train + test run of every grid point is logged
    assert trials.count() == w * grid * 2
    assert len(study.per_period_srs) == w * grid  # one per train evaluation
    # selected params are always a real grid point
    for row in study.windows:
        assert set(row.selected_params) == {"n_hold", "buffer"}
        assert "sharpe" in row.oos and "total_return" in row.benchmark
    # plateau surface: one entry per parameter, keyed by value
    assert set(study.plateau) == {"n_hold", "buffer"}
    assert set(study.plateau["n_hold"]) == {"3", "5"}
    assert 0.0 <= study.pct_positive_windows <= 1.0
    assert "sharpe" in study.combined_oos and "sharpe" in study.combined_benchmark
    assert len(study.combined_oos_returns) > 0


def test_build_report_renders_gate_and_stress(study_env, tmp_path):
    trials = TrialLog(tmp_path / "trials.csv")
    base = _run(study_env, trials)
    stress = _run(study_env, trials, cost_mult=2.0)  # doubled-cost line for the same candidate
    report = build_report([base, stress], trials, data_note="Synthetic.", generated="2026-07-03")
    assert "# Tradehelm - Strategy Research Report" in report
    assert "Gate 3G - preliminary verdict" in report
    assert "candidate_c" in report
    assert "Deflated Sharpe" in report
    assert "costs x2" in report  # the stress line is folded in
    assert "Trials executed" in report
    # a verdict is stated one way or the other
    assert ("PASS" in report) or ("No candidate passes" in report)


def test_study_result_dataclass_defaults():
    s = CandidateStudy("candidate_a", 1.0, "27/42", [], {}, {}, 0.0, {})
    assert s.n_configs == 0 and list(s.per_period_srs) == []
