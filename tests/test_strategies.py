"""Behavioural tests for the v1 candidates, the shared sizing/slot logic, and the
engine's weight=None HOLD semantic. Fixtures are hand-built adjusted frames fed
through StrategyContext directly, so each rule is exercised in isolation."""

import numpy as np
import pandas as pd
import pytest

from tradehelm.backtest import BacktestEngine, CostModel
from tradehelm.backtest.engine import Portfolio, StrategyContext, TargetPosition
from tradehelm.config import CostConfig
from tradehelm.data import TradingCalendar
from tradehelm.strategy import (
    CandidateA,
    CandidateB,
    CandidateC,
    PositionBook,
    RiskParams,
)
from tradehelm.strategy import indicators as ind
from tradehelm.strategy.base import (
    EntrySignal,
    allocate_new_entries,
    current_exposure_frac,
    is_week_end_session,
    risk_weight,
)

RISK = RiskParams(max_positions=5, per_position_risk_frac=0.01, max_position_notional_frac=0.30)
NOCOST = CostConfig(
    commission_rate_us=0.0,
    min_commission_us=0.0,
    half_spread_bps=0.0,
    slippage_bps=0.0,
    fx_conversion_rate=0.0,
    custody_fee_annual=0.0,
)


def adj(closes, index=None, vol=3_000_000.0):
    """An adjusted-OHLC frame (the shape StrategyContext.history returns)."""
    closes = np.asarray(closes, dtype=float)
    if index is None:
        index = pd.bdate_range("2018-01-01", periods=len(closes))
    s = pd.Series(closes, index=index)
    return pd.DataFrame(
        {
            "open": s.shift(1).fillna(s.iloc[0]),
            "high": s * 1.01,
            "low": s * 0.99,
            "close": s,
            "volume": vol,
            "dollar_volume": s * vol,
        },
        index=index,
    )


def _uptrend(n, lo=100.0, hi=150.0):
    return list(np.linspace(lo, hi, n))


def _ctx(frames, date, members, portfolio=None):
    portfolio = portfolio or Portfolio(cash_usd=100_000.0)
    return StrategyContext(frames, date, members, portfolio)


# --------------------------------------------------------------------------- A


def _pullback_frames():
    """SPY and AAA both in uptrends; AAA has a sharp 2-day pullback at the end
    (RSI(2) very low, close < SMA(5)) -> a Candidate A entry setup."""
    aaa = _uptrend(220, 50, 90)
    aaa = aaa[:-2] + [aaa[-3] * 0.97, aaa[-3] * 0.94]
    fa = adj(aaa)
    spy = adj(_uptrend(len(fa)), index=fa.index)
    return {"AAA": fa, "SPY": spy}, fa.index[-1]


def test_candidate_a_enters_on_trend_pullback():
    frames, date = _pullback_frames()
    ctx = _ctx(frames, date, ["AAA"])
    targets = CandidateA(risk=RISK).target_positions(ctx)
    assert [t.symbol for t in targets] == ["AAA"]
    t = targets[0]
    close = frames["AAA"]["close"]
    atr14 = float(ind.atr(frames["AAA"], 14).iloc[-1])
    c = float(close.iloc[-1])
    assert t.stop_price == pytest.approx(c - 3.0 * atr14)  # entry - stop_atr*ATR
    assert t.weight == pytest.approx(risk_weight(c, t.stop_price, 0.01, 0.30))


def test_candidate_a_no_entry_when_market_regime_off():
    frames, date = _pullback_frames()
    frames["SPY"] = adj(_uptrend(len(frames["AAA"]), 150, 100), index=frames["AAA"].index)
    ctx = _ctx(frames, date, ["AAA"])
    assert CandidateA(risk=RISK).target_positions(ctx) == []  # SPY below its SMA(200)


def test_candidate_a_exits_on_overbought():
    aaa = _uptrend(220, 50, 90)  # strictly rising -> last bar RSI(2)=100 > 70
    frames = {"AAA": adj(aaa), "SPY": adj(_uptrend(220))}
    date = frames["AAA"].index[-1]
    pf = Portfolio(cash_usd=10_000.0, shares={"AAA": 100.0})
    targets = CandidateA(risk=RISK).target_positions(_ctx(frames, date, ["AAA"], pf))
    assert "AAA" not in {t.symbol for t in targets}  # overbought -> exit (omitted)


def test_candidate_a_holds_with_entry_relative_stop():
    frames, date = _pullback_frames()  # pullback -> no exit signal for a holder
    pf = Portfolio(cash_usd=10_000.0, shares={"AAA": 100.0})
    strat = CandidateA(risk=RISK)
    targets = strat.target_positions(_ctx(frames, date, ["AAA"], pf))
    hold = next(t for t in targets if t.symbol == "AAA")
    assert hold.weight is None  # HOLD, not resized
    lot = strat.book.lot("AAA")
    assert hold.stop_price == pytest.approx(lot.entry_price - 3.0 * lot.entry_atr)


# --------------------------------------------------------------------------- B


def test_candidate_b_enters_on_breakout():
    aaa = _uptrend(210, 40, 120)  # last bar is a fresh 20-day high (>=200 bars for regime)
    frames = {"AAA": adj(aaa), "SPY": adj(_uptrend(210))}
    date = frames["AAA"].index[-1]
    targets = CandidateB(risk=RISK, entry_lookback=20).target_positions(_ctx(frames, date, ["AAA"]))
    assert [t.symbol for t in targets] == ["AAA"]
    c = float(frames["AAA"]["close"].iloc[-1])
    atr14 = float(ind.atr(frames["AAA"], 14).iloc[-1])
    assert targets[0].stop_price == pytest.approx(c - 3.0 * atr14)


def test_candidate_b_exits_on_channel_breakdown():
    aaa = _uptrend(60, 40, 100) + [70.0]  # cliff below the prior 10-day low
    frames = {"AAA": adj(aaa), "SPY": adj(_uptrend(61))}
    date = frames["AAA"].index[-1]
    pf = Portfolio(cash_usd=10_000.0, shares={"AAA": 100.0})
    strat = CandidateB(risk=RISK, exit_lookback=10)
    targets = strat.target_positions(_ctx(frames, date, ["AAA"], pf))
    assert "AAA" not in {t.symbol for t in targets}


def test_position_book_stop_only_ratchets_up():
    book = PositionBook()
    frames = {"AAA": adj(_uptrend(30, 40, 60))}
    pf = Portfolio(cash_usd=0.0, shares={"AAA": 10.0})
    ctx = _ctx(frames, frames["AAA"].index[-1], ["AAA"], pf)
    book.sync(ctx)
    assert book.ratchet_stop("AAA", 50.0) == 50.0
    assert book.ratchet_stop("AAA", 55.0) == 55.0  # rises
    assert book.ratchet_stop("AAA", 51.0) == 55.0  # never falls back


# --------------------------------------------------------------------------- C


def _momentum_panel(cal, n_names=6):
    sessions = cal.sessions("2019-01-02", "2021-06-30")
    # a decision date that is genuinely a week's last session, with enough history
    week_ends = [s for s in sessions[300:] if is_week_end_session(cal, s)]
    date = week_ends[0]
    idx = sessions[: sessions.get_loc(date) + 1]
    frames = {"SPY": adj(list(100 * np.exp(np.linspace(0, 0.1, len(idx)))), index=idx)}
    # M5 strongest ... M0 weakest (distinct slopes -> deterministic ranking).
    for i in range(n_names):
        slope = 0.1 * i + 0.05
        frames[f"M{i}"] = adj(list(50 * np.exp(np.linspace(0, slope, len(idx)))), index=idx)
    return frames, date, idx


def test_candidate_c_rotates_into_top_ranked():
    cal = TradingCalendar()
    frames, date, _ = _momentum_panel(cal)
    members = [f"M{i}" for i in range(6)]
    targets = CandidateC(risk=RISK, n_hold=3, calendar=cal).target_positions(
        _ctx(frames, date, members)
    )
    picked = {t.symbol for t in targets}
    assert picked == {"M5", "M4", "M3"}  # the three highest blended-momentum names


def test_candidate_c_buffer_keeps_laggard_and_sells_dropout():
    cal = TradingCalendar()
    frames, date, _ = _momentum_panel(cal)
    members = [f"M{i}" for i in range(6)]
    pf = Portfolio(cash_usd=50_000.0, shares={"M3": 100.0, "M0": 100.0})
    targets = CandidateC(risk=RISK, n_hold=2, buffer=2.0, calendar=cal).target_positions(
        _ctx(frames, date, members, pf)
    )
    by_symbol = {t.symbol: t for t in targets}
    assert by_symbol["M3"].weight is None  # within top-(2*2) buffer band -> held
    assert "M0" not in by_symbol  # dropped out of the band -> sold
    assert "M5" in by_symbol  # a free slot filled by the top name


def test_candidate_c_holds_between_weekly_decisions():
    cal = TradingCalendar()
    frames, _, idx = _momentum_panel(cal)
    midweek = next(s for s in idx[::-1] if not is_week_end_session(cal, s))
    hist = {k: v.loc[:midweek] for k, v in frames.items()}
    pf = Portfolio(cash_usd=10_000.0, shares={"M3": 100.0})
    targets = CandidateC(risk=RISK, n_hold=3, calendar=cal).target_positions(
        _ctx(hist, midweek, [f"M{i}" for i in range(6)], pf)
    )
    assert [t.symbol for t in targets] == ["M3"] and targets[0].weight is None


def test_candidate_c_goes_to_cash_when_regime_off():
    cal = TradingCalendar()
    frames, date, idx = _momentum_panel(cal)
    frames["SPY"] = adj(list(150 * np.exp(np.linspace(0, -0.3, len(idx)))), index=idx)  # downtrend
    pf = Portfolio(cash_usd=10_000.0, shares={"M3": 100.0})
    targets = CandidateC(risk=RISK, calendar=cal).target_positions(
        _ctx(frames, date, [f"M{i}" for i in range(6)], pf)
    )
    assert targets == []  # sell everything, hold cash


def test_is_week_end_session_handles_holiday_shift():
    cal = TradingCalendar()
    # 2020-12-31 (Thu) is a week's last session (Jan 1 holiday, then the weekend).
    assert is_week_end_session(cal, pd.Timestamp("2020-12-31"))
    assert not is_week_end_session(cal, pd.Timestamp("2020-12-30"))  # mid-week


# ------------------------------------------------------------------- sizing


def test_risk_weight_formula_cap_and_ill_posed():
    assert risk_weight(100.0, 90.0, 0.01, 0.30) == pytest.approx(0.01 * 100 / 10)  # 0.10
    assert risk_weight(100.0, 99.0, 0.01, 0.30) == 0.30  # capped at max notional
    assert risk_weight(100.0, 100.0, 0.01, 0.30) == 0.0  # stop not below entry
    assert risk_weight(100.0, 120.0, 0.01, 0.30) == 0.0


def test_allocate_respects_slots_and_no_leverage():
    sigs = [EntrySignal(f"S{i}", entry_ref=100.0, stop=95.0, rank_key=i) for i in range(6)]
    risk = RiskParams(max_positions=4, per_position_risk_frac=0.05, max_position_notional_frac=0.25)
    # weight per name = 0.05*100/5 = 0.10; two slots free (n_kept=2).
    out = allocate_new_entries(sigs, risk, n_kept=2, existing_exposure=0.0)
    assert [s for s, _, _ in out] == ["S0", "S1"]  # respects the 2 free slots, best-first
    # exposure cap: already 0.95 invested, one 0.10 name would breach 1.0 -> none added.
    assert allocate_new_entries(sigs, risk, n_kept=0, existing_exposure=0.95) == []


def test_current_exposure_frac():
    frames = {"AAA": adj([10.0] * 30)}
    pf = Portfolio(cash_usd=500.0, shares={"AAA": 50.0})  # 500 stock / 1000 equity
    ctx = _ctx(frames, frames["AAA"].index[-1], ["AAA"], pf)
    assert current_exposure_frac(ctx, {"AAA"}) == pytest.approx(0.5)
    assert current_exposure_frac(ctx, set()) == pytest.approx(0.0)


# ---------------------------------------------------------- engine HOLD path


class _HoldOnce:
    """Buy AAA fully on the first fill, then HOLD (weight=None) forever."""

    name = "hold_once"

    def target_positions(self, ctx):
        if ctx.portfolio.held("AAA") > 0:
            return [TargetPosition("AAA", weight=None)]
        return [TargetPosition("AAA", weight=1.0)]


def test_engine_hold_does_not_resize_on_price_drift():
    cal = TradingCalendar()
    sessions = cal.sessions("2021-01-04", "2021-02-01")
    n = len(sessions)
    # a strong uptrend: equity and price both drift, which would churn a resizer
    closes = list(100 * np.exp(np.linspace(0, 0.4, n)))
    raw = pd.DataFrame(
        {
            "open": closes,
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": closes,
            "adj_close": closes,
            "volume": [5_000_000] * n,
        },
        index=sessions,
    )
    eng = BacktestEngine(cal, CostModel(NOCOST), {2021: 1e12})
    res = eng.run(
        _HoldOnce(), {"AAA": raw}, lambda _d: ["AAA"], sessions[0], sessions[-1], 1e6, 7.0
    )
    buys = [t for t in res.trades if t.side > 0]
    assert len(buys) == 1  # bought once, never resized despite the price/equity climb
    assert not [t for t in res.trades if t.side < 0]  # and never sold
