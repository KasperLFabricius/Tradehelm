"""Phase 3c - tests for the Fable review fixes (docs/REVIEW_PHASE_0-3.md).

F1 precomputed indicators == prefix-recompute; F2 Candidate C equal-ish sizing;
F3 min-ticket skip; F4 custody accrual; F6 report n/a for an un-run stress line;
F8 pay-tax fails loud when equity < tax."""

import numpy as np
import pandas as pd
import pytest

from tradehelm.backtest import BacktestEngine, CostModel
from tradehelm.backtest.engine import Portfolio, StrategyContext, TargetPosition
from tradehelm.config import CostConfig
from tradehelm.data import TradingCalendar
from tradehelm.strategy import CandidateA, CandidateB, CandidateC, RiskParams
from tradehelm.strategy.base import is_week_end_session
from tradehelm.strategy.features import build_features, resolve_feature

RISK = RiskParams(max_positions=5, per_position_risk_frac=0.01, max_position_notional_frac=0.30)
NOCOST = CostConfig(
    commission_rate_us=0.0,
    min_commission_us=0.0,
    half_spread_bps=0.0,
    slippage_bps=0.0,
    fx_conversion_rate=0.0,
    custody_fee_annual=0.0,
)


def _adj(closes, index):
    s = pd.Series(np.asarray(closes, float), index=index)
    return pd.DataFrame(
        {
            "open": s.shift(1).fillna(s.iloc[0]),
            "high": s * 1.01,
            "low": s * 0.99,
            "close": s,
            "volume": 4_000_000.0,
            "dollar_volume": s * 4_000_000.0,
        },
        index=index,
    )


@pytest.fixture(scope="module")
def panel_env():
    cal = TradingCalendar()
    sessions = cal.sessions("2018-01-02", "2019-12-31")  # ~500 sessions
    n = len(sessions)
    rng = np.random.default_rng(2)
    # SPY a clean uptrend so the SMA(200) regime is ON in the latter half (else every
    # candidate gates all entries and the equivalence is trivially empty).
    adjusted = {"SPY": _adj(100 * np.exp(np.linspace(0, 0.4, n)), sessions)}
    for i in range(6):
        drift = 4e-4 + 1e-4 * i
        adjusted[f"M{i}"] = _adj(
            (40 + 8 * i) * np.exp(np.cumsum(rng.normal(drift, 0.018, n))), sessions
        )
    return cal, adjusted, sessions, [f"M{i}" for i in range(6)]


def _targets_key(targets):
    def stop(t):
        return None if t.stop_price is None else round(t.stop_price, 6)

    def w(t):
        return None if t.weight is None else round(t.weight, 8)

    return sorted((t.symbol, w(t), stop(t), t.reason) for t in targets)


# --------------------------------------------------------------- F1 equivalence


@pytest.mark.parametrize("factory", ["a", "b", "c"])
def test_precomputed_features_match_prefix_recompute(panel_env, factory):
    cal, adjusted, sessions, members = panel_env
    features = build_features(adjusted)

    def make():
        if factory == "a":
            return CandidateA(risk=RISK)
        if factory == "b":
            return CandidateB(risk=RISK)
        return CandidateC(risk=RISK, n_hold=3, calendar=cal)

    # Scan many decision dates in the latter half (enough history for every indicator);
    # on every one, the precomputed-feature path must match the on-demand recompute, and
    # across the scan at least one date must produce a target (non-trivial equivalence).
    any_targets = False
    for date in sessions[300::7]:
        ctx_fast = StrategyContext(
            adjusted, date, members, Portfolio(cash_usd=100_000.0), features=features
        )
        ctx_slow = StrategyContext(adjusted, date, members, Portfolio(cash_usd=100_000.0))
        fast = make().target_positions(ctx_fast)
        slow = make().target_positions(ctx_slow)  # features=None -> recompute on demand
        assert _targets_key(fast) == _targets_key(slow)
        any_targets = any_targets or bool(fast)
    assert any_targets, "expected targets on at least one scanned date"


def test_full_backtest_identical_with_and_without_precomputed_features():
    # The definitive F1 guarantee: a whole backtest (entries, holds, stops, custody,
    # tax) run with the precomputed feature panel must equal the same backtest that
    # recomputes features on demand - identical equity curve and trades.
    from tradehelm.backtest.engine import adjusted_ohlc
    from tradehelm.strategy.features import build_features

    cal = TradingCalendar()
    sessions = cal.sessions("2018-01-02", "2019-12-31")
    n = len(sessions)
    rng = np.random.default_rng(9)
    raw = {"SPY": None}
    for sym in ("SPY", *[f"M{i}" for i in range(6)]):
        start = 100.0 if sym == "SPY" else 40.0 + 8 * int(sym[1:])
        drift = 5e-4 if sym == "SPY" else 4e-4 + 1e-4 * int(sym[1:])
        close = pd.Series(start * np.exp(np.cumsum(rng.normal(drift, 0.014, n))), index=sessions)
        op = close.shift(1).fillna(close.iloc[0])
        raw[sym] = pd.DataFrame(
            {
                "open": op,
                "high": close * 1.01,
                "low": close * 0.99,
                "close": close,
                "adj_close": close,
                "volume": [4_000_000] * n,
            },
            index=sessions,
        )
    members = [f"M{i}" for i in range(6)]
    costs = CostModel(
        CostConfig(
            commission_rate_us=8e-4,
            min_commission_us=1.0,
            half_spread_bps=2.0,
            slippage_bps=1.0,
            fx_conversion_rate=2.5e-3,
            custody_fee_annual=0.005,
        )
    )
    eng = BacktestEngine(cal, costs, {y: 6e4 for y in range(2018, 2020)}, min_ticket_dkk=1500.0)
    args = (raw, lambda _d: members, "2019-01-02", "2019-12-31", 100_000.0, 7.0)

    slow = eng.run(CandidateA(risk=RISK), *args)  # features rebuilt internally on demand
    adjusted = {s: adjusted_ohlc(df) for s, df in raw.items()}
    fast = eng.run(
        CandidateA(risk=RISK), *args, adjusted=adjusted, features=build_features(adjusted)
    )

    assert len(slow.trades) == len(fast.trades) > 0
    assert (slow.equity_dkk - fast.equity_dkk).abs().max() < 1e-6
    assert slow.final_equity_dkk == pytest.approx(fast.final_equity_dkk)


def test_resolve_feature_parametric_and_unknown():
    idx = pd.bdate_range("2020-01-01", periods=60)
    frame = _adj(np.linspace(10, 40, 60), idx)
    assert resolve_feature("sma5")(frame).iloc[-1] == pytest.approx(frame["close"].iloc[-5:].mean())
    assert resolve_feature("highest_20")(frame).iloc[-1] == frame["close"].iloc[-20:].max()
    # lowest_10_prev excludes today (shifted by one)
    assert resolve_feature("lowest_10_prev")(frame).iloc[-1] == frame["close"].iloc[-11:-1].min()
    with pytest.raises(KeyError):
        resolve_feature("bogus_indicator")


# --------------------------------------------------------------------- F2 sizing


def test_candidate_c_uses_equal_ish_sizing_not_risk_based(panel_env):
    cal, adjusted, sessions, members = panel_env
    features = build_features(adjusted)
    date = next(s for s in reversed(sessions) if is_week_end_session(cal, s))
    ctx = StrategyContext(adjusted, date, members, Portfolio(cash_usd=100_000.0), features=features)
    entries = [
        t
        for t in CandidateC(risk=RISK, n_hold=5, calendar=cal).target_positions(ctx)
        if t.weight is not None
    ]
    assert entries, "expected C to open positions on a decision day"
    expected = min(1.0 / 5, RISK.max_position_notional_frac)  # 1/n_hold = 0.20
    for t in entries:
        assert t.weight == pytest.approx(expected)  # NOT the ~5% a 20%-stop risk size gives


# ----------------------------------------------------------------- F3 min-ticket


class _TargetOne:
    name = "one"

    def __init__(self, symbol, weight):
        self.symbol, self.weight = symbol, weight

    def target_positions(self, ctx):
        if ctx.portfolio.held(self.symbol) > 0:
            return [TargetPosition(self.symbol, weight=None)]
        return [TargetPosition(self.symbol, weight=self.weight)]


def _flat_panel(cal):
    sessions = cal.sessions("2021-01-04", "2021-02-01")
    n = len(sessions)
    raw = pd.DataFrame(
        {
            "open": 100.0,
            "high": 100.0,
            "low": 100.0,
            "close": 100.0,
            "adj_close": 100.0,
            "volume": [5_000_000] * n,
        },
        index=sessions,
    )
    return sessions, {"AAA": raw}


def test_min_ticket_skips_subscale_buys():
    cal = TradingCalendar()
    sessions, panel = _flat_panel(cal)
    # weight 0.001 of 1,000,000 DKK ~= 1,000 DKK notional, below a 2,000 DKK ticket.
    eng = BacktestEngine(cal, CostModel(NOCOST), {2021: 1e12}, min_ticket_dkk=2000.0)
    res = eng.run(
        _TargetOne("AAA", 0.001),
        panel,
        lambda _d: ["AAA"],
        sessions[0],
        sessions[-1],
        1_000_000.0,
        7.0,
    )
    assert not [t for t in res.trades if t.side > 0]  # sub-ticket -> no buy
    eng0 = BacktestEngine(cal, CostModel(NOCOST), {2021: 1e12}, min_ticket_dkk=0.0)
    res0 = eng0.run(
        _TargetOne("AAA", 0.001),
        panel,
        lambda _d: ["AAA"],
        sessions[0],
        sessions[-1],
        1_000_000.0,
        7.0,
    )
    assert [t for t in res0.trades if t.side > 0]  # with the gate off, it buys


# ------------------------------------------------------------------- F4 custody


def test_custody_fee_charged_on_holdings():
    cal = TradingCalendar()
    sessions, panel = _flat_panel(cal)
    thresholds = {2021: 1e12}
    base = BacktestEngine(cal, CostModel(NOCOST), thresholds)
    fee_cfg = CostConfig(
        commission_rate_us=0.0,
        min_commission_us=0.0,
        half_spread_bps=0.0,
        slippage_bps=0.0,
        fx_conversion_rate=0.0,
        custody_fee_annual=0.0126,  # ~ 5bps/session
    )
    charged = BacktestEngine(cal, CostModel(fee_cfg), thresholds)
    args = (_TargetOne("AAA", 1.0), panel, lambda _d: ["AAA"], sessions[0], sessions[-1], 1e6, 7.0)
    no_fee = base.run(*args).final_equity_dkk
    with_fee = charged.run(*args).final_equity_dkk
    assert with_fee < no_fee  # custody drags the after-fee curve down
    # ~ one session's fee per held session (flat price, fully invested)
    sessions_held = len(sessions) - 1
    approx = 1e6 * 0.0126 / 252 * sessions_held
    assert (no_fee - with_fee) == pytest.approx(approx, rel=0.05)


# ------------------------------------------------------------------ F8 fail loud


def test_pay_tax_raises_when_equity_below_bill():
    cal = TradingCalendar()
    eng = BacktestEngine(cal, CostModel(NOCOST), {2021: 1e12})
    from tradehelm.backtest.tax import DanishTaxLedger

    portfolio = Portfolio(cash_usd=10.0)  # no holdings, tiny cash
    tax = DanishTaxLedger({2021: 1e12})
    with pytest.raises(ValueError, match="exceeds liquidatable equity"):
        eng._pay_tax(50_000.0, portfolio, lambda _d: 7.0, pd.Timestamp("2021-06-01"), {}, tax, [])
