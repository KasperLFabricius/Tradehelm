"""Backtest engine tests: hand-computed golden run, anti-lookahead, tax + stops."""

import pandas as pd
import pytest

from tradehelm.backtest import (
    BacktestEngine,
    CostModel,
    LookaheadError,
    TargetPosition,
    progressive_tax,
)
from tradehelm.config import CostConfig
from tradehelm.data import TradingCalendar

CAL = TradingCalendar()
THRESHOLDS = {2021: 79_400.0}


def _zero_costs():
    return CostConfig(
        commission_rate_us=0.0,
        min_commission_us=0.0,
        half_spread_bps=0.0,
        slippage_bps=0.0,
        fx_conversion_rate=0.0,
        custody_fee_annual=0.0,
    )


def _bars(index, opens, closes, lows=None):
    n = len(index)
    lows = lows if lows is not None else list(closes)
    return pd.DataFrame(
        {
            "open": opens,
            "high": [max(o, c) for o, c in zip(opens, closes, strict=True)],
            "low": lows,
            "close": closes,
            "adj_close": closes,  # no dividends -> adjusted == raw
            "volume": [1] * n,
        },
        index=index,
    )


class BuyAndHold:
    name = "buy_and_hold"

    def __init__(self, symbol):
        self.symbol = symbol

    def target_positions(self, ctx):
        return [TargetPosition(self.symbol, 1.0)]


def test_golden_buy_and_hold_including_tax():
    sessions = CAL.sessions("2021-01-04", "2021-01-11")  # 6 sessions
    n = len(sessions)
    opens = [100.0] * n
    closes = [100.0] * (n - 1) + [120.0]  # flat, then an unrealized jump on the last close
    panel = {"AAA": _bars(sessions, opens, closes)}
    engine = BacktestEngine(CAL, CostModel(_zero_costs()), THRESHOLDS)

    res = engine.run(
        BuyAndHold("AAA"),
        panel,
        lambda _d: ["AAA"],
        "2021-01-04",
        "2021-01-11",
        initial_dkk=100_000.0,
        usd_dkk=7.0,
    )
    # 100000/7 = 14285.71 USD -> buy floor(14285.71/100)=142 @100 -> cash 85.71.
    # final = (85.71 + 142*120)*7 = 17125.71*7 = 119880 DKK. Never sold -> tax 0.
    assert res.final_equity_dkk == pytest.approx(119_880.0, rel=1e-6)
    assert res.tax_by_year[2021] == pytest.approx(0.0)  # realisation principle: unrealized untaxed


class BuyOnceThenExit:
    name = "buy_once_then_exit"

    def __init__(self, symbol):
        self.symbol = symbol
        self._entered = False

    def target_positions(self, ctx):
        if not self._entered:
            self._entered = True
            return [TargetPosition(self.symbol, 1.0)]
        return []  # exit on every later decision


def test_realized_gain_is_taxed_and_deducted():
    sessions = CAL.sessions("2021-01-04", "2021-01-11")
    n = len(sessions)
    opens = [100.0, 100.0, 200.0] + [200.0] * (n - 3)  # buy @100 (S1), sell @200 (S2)
    closes = list(opens)
    panel = {"AAA": _bars(sessions, opens, closes)}
    engine = BacktestEngine(CAL, CostModel(_zero_costs()), THRESHOLDS)

    res = engine.run(
        BuyOnceThenExit("AAA"),
        panel,
        lambda _d: ["AAA"],
        "2021-01-04",
        "2021-01-11",
        initial_dkk=100_000.0,
        usd_dkk=7.0,
    )
    # Buy 142 @100 (basis 700 DKK/sh), sell 142 @200 (1400 DKK/sh) -> gain 99,400 DKK.
    expected_tax = progressive_tax(99_400.0, 79_400.0, 0.27, 0.42)  # 29,838
    assert res.tax_by_year[2021] == pytest.approx(expected_tax)
    # cash before tax = 85.71 + 142*200 = 28485.71 USD -> *7 = 199,400 DKK; minus tax.
    assert res.final_equity_dkk == pytest.approx(199_400.0 - expected_tax, rel=1e-6)


def test_all_in_target_does_not_overdraw_with_costs():
    sessions = CAL.sessions("2021-01-04", "2021-01-08")
    n = len(sessions)
    panel = {"AAA": _bars(sessions, [100.0] * n, [100.0] * n)}
    costs = CostConfig(
        commission_rate_us=0.0008,
        min_commission_us=1.0,
        half_spread_bps=2.5,
        slippage_bps=2.5,
        fx_conversion_rate=0.0,  # keep funding clean: 99400/7 = exactly 14200 USD
        custody_fee_annual=0.0,
    )
    engine = BacktestEngine(CAL, CostModel(costs), THRESHOLDS)
    res = engine.run(
        BuyAndHold("AAA"), panel, lambda _d: ["AAA"], "2021-01-04", "2021-01-08", 99_400.0, 7.0
    )
    funded_usd = 99_400.0 / 7.0  # 14200
    buy = next(t for t in res.trades if t.side == 1)
    # Naive sizing (14200/100 = 142) would cost 142*100.05 + commission > 14200 and go
    # negative; the cap buys one fewer share so the fill fits the cash.
    assert buy.shares == 141
    assert buy.shares * buy.price_usd + buy.commission_usd <= funded_usd + 1e-9


def test_tax_payment_charges_fx_conversion_fee():
    sessions = CAL.sessions("2021-01-04", "2021-01-11")
    n = len(sessions)
    opens = [100.0, 100.0, 200.0] + [200.0] * (n - 3)  # buy @100 (S1), sell @200 (S2)
    panel = {"AAA": _bars(sessions, opens, list(opens))}
    costs = CostConfig(
        commission_rate_us=0.0,
        min_commission_us=0.0,
        half_spread_bps=0.0,
        slippage_bps=0.0,
        fx_conversion_rate=0.0025,
        custody_fee_annual=0.0,
    )
    engine = BacktestEngine(CAL, CostModel(costs), THRESHOLDS)
    res = engine.run(
        BuyOnceThenExit("AAA"),
        panel,
        lambda _d: ["AAA"],
        "2021-01-04",
        "2021-01-11",
        100_000.0,
        7.0,
    )
    # funded 99,750 DKK -> 14250 USD; buy 142@100, sell 142@200 -> cash 28450 USD; gain 99,400.
    assert res.tax_by_year[2021] == pytest.approx(29_838.0)
    # Tax paid as USD->DKK also incurs the 0.25% FX fee: (tax * 1.0025) / 7 USD deducted.
    expected_dkk = (28_450.0 - (29_838.0 * 1.0025) / 7.0) * 7.0
    assert res.final_equity_dkk == pytest.approx(expected_dkk, rel=1e-9)


def test_year_end_tax_settled_before_new_year():
    thresholds = {2020: 55_300.0, 2021: 79_400.0}
    sessions = CAL.sessions("2020-12-28", "2021-01-08")  # spans the year boundary
    n = len(sessions)
    opens = [100.0, 100.0, 200.0] + [200.0] * (n - 3)  # buy @100, sell @200, all in 2020
    panel = {"AAA": _bars(sessions, opens, list(opens))}
    engine = BacktestEngine(CAL, CostModel(_zero_costs()), thresholds)
    res = engine.run(
        BuyOnceThenExit("AAA"),
        panel,
        lambda _d: ["AAA"],
        "2020-12-28",
        "2021-01-08",
        100_000.0,
        7.0,
    )
    # 2020 realized gain 99,400 DKK -> tax at the 2020 threshold, settled at the rollover.
    assert res.tax_by_year[2020] == pytest.approx(progressive_tax(99_400.0, 55_300.0, 0.27, 0.42))
    assert res.tax_by_year[2020] > 0


def test_held_position_missing_bar_marks_at_last_price():
    # AAA has no bar on the final session (a delisting tail). The held position must be
    # valued at its last close, not silently dropped to zero.
    sessions = CAL.sessions("2021-01-04", "2021-01-08")  # 5 sessions
    idx4 = sessions[:4]
    aaa = _bars(idx4, [100.0] * 4, [100.0, 100.0, 100.0, 110.0])  # last close 110 on S3
    engine = BacktestEngine(CAL, CostModel(_zero_costs()), THRESHOLDS)
    res = engine.run(
        BuyAndHold("AAA"),
        {"AAA": aaa},
        lambda _d: ["AAA"],
        "2021-01-04",
        "2021-01-08",
        100_000.0,
        7.0,
    )
    # buy 142 @100 -> cash 85.71; value at last close 110 on the missing day:
    # (85.71 + 142*110) * 7 = 109,940 DKK (not 600 DKK if the position were zeroed).
    assert res.final_equity_dkk == pytest.approx(109_940.0, rel=1e-6)


class NoTrade:
    name = "no_trade"

    def target_positions(self, ctx):
        return []


def test_funding_fx_fee_shows_as_return_drag():
    from tradehelm.backtest import metrics

    sessions = CAL.sessions("2021-01-04", "2021-01-08")
    n = len(sessions)
    panel = {"AAA": _bars(sessions, [100.0] * n, [100.0] * n)}
    costs = CostConfig(
        commission_rate_us=0.0,
        min_commission_us=0.0,
        half_spread_bps=0.0,
        slippage_bps=0.0,
        fx_conversion_rate=0.0025,
        custody_fee_annual=0.0,
    )
    engine = BacktestEngine(CAL, CostModel(costs), THRESHOLDS)
    res = engine.run(
        NoTrade(), panel, lambda _d: ["AAA"], "2021-01-04", "2021-01-08", 100_000.0, 7.0
    )
    # Equity starts at the gross 100,000 DKK; a no-trade run funded at 0.25% FX fee
    # ends at 99,750 DKK -> total return -0.25% (the funding cost is not rebased away).
    assert res.equity_dkk.iloc[0] == pytest.approx(100_000.0)
    assert metrics.total_return(res.equity_dkk) == pytest.approx(-0.0025, abs=1e-9)


def test_sizing_uses_decision_close_not_fill_open():
    # Decision at S0 (close 100); S1 opens gapped down to 50. The order quantity must be
    # sized from the decision close (no lookahead), then filled at the open.
    sessions = CAL.sessions("2021-01-04", "2021-01-08")
    n = len(sessions)
    opens = [100.0, 50.0] + [50.0] * (n - 2)
    closes = [100.0] + [50.0] * (n - 1)
    panel = {"AAA": _bars(sessions, opens, closes)}
    engine = BacktestEngine(CAL, CostModel(_zero_costs()), THRESHOLDS)
    res = engine.run(
        BuyOnceThenExit("AAA"),
        panel,
        lambda _d: ["AAA"],
        "2021-01-04",
        "2021-01-08",
        100_000.0,
        7.0,
    )
    buy = next(t for t in res.trades if t.side == 1)
    # int(14285.71 / 100) = 142 from the decision close - NOT int(14285.71 / 50) = 285.
    assert buy.shares == 142
    assert buy.price_usd == pytest.approx(50.0)  # but filled at the gapped-down open


class TargetOutsideUniverse:
    name = "outside_universe"

    def target_positions(self, ctx):
        return [TargetPosition("BBB", 1.0)]  # BBB is not in the point-in-time universe


def test_targets_outside_universe_are_dropped():
    sessions = CAL.sessions("2021-01-04", "2021-01-08")
    n = len(sessions)
    panel = {
        "AAA": _bars(sessions, [100.0] * n, [100.0] * n),
        "BBB": _bars(
            sessions, [100.0] * n, [100.0] * n
        ),  # exists in the panel but not the universe
    }
    engine = BacktestEngine(CAL, CostModel(_zero_costs()), THRESHOLDS)
    res = engine.run(
        TargetOutsideUniverse(),
        panel,
        lambda _d: ["AAA"],
        "2021-01-04",
        "2021-01-08",
        100_000.0,
        7.0,
    )
    # The universe is only AAA, so a target for BBB is never traded (survivorship safety).
    assert not any(t.symbol == "BBB" for t in res.trades)


class PeekAhead:
    name = "peek"

    def __init__(self, symbol, future_date):
        self.symbol = symbol
        self.future_date = future_date

    def target_positions(self, ctx):
        ctx.close_on(self.symbol, self.future_date)  # must raise
        return []


def test_lookahead_is_blocked():
    sessions = CAL.sessions("2021-01-04", "2021-01-08")
    panel = {"AAA": _bars(sessions, [100.0] * len(sessions), [100.0] * len(sessions))}
    engine = BacktestEngine(CAL, CostModel(_zero_costs()), THRESHOLDS)
    strat = PeekAhead("AAA", sessions[-1])  # decision at sessions[0] peeks at the last day
    with pytest.raises(LookaheadError):
        engine.run(strat, panel, lambda _d: ["AAA"], "2021-01-04", "2021-01-08", 100_000.0, 7.0)


class HoldWithStop:
    name = "hold_with_stop"

    def __init__(self, symbol, stop):
        self.symbol = symbol
        self.stop = stop

    def target_positions(self, ctx):
        return [TargetPosition(self.symbol, 1.0, stop_price=self.stop)]


def test_protective_stop_exits_on_intraday_touch():
    sessions = CAL.sessions("2021-01-04", "2021-01-08")  # 5 sessions
    n = len(sessions)
    opens = [100.0] * n
    closes = [100.0] * n
    lows = [100.0, 100.0, 80.0] + [100.0] * (n - 3)  # S2 dips to 80, below the 90 stop
    panel = {"AAA": _bars(sessions, opens, closes, lows=lows)}
    engine = BacktestEngine(CAL, CostModel(_zero_costs()), THRESHOLDS)

    res = engine.run(
        HoldWithStop("AAA", 90.0),
        panel,
        lambda _d: ["AAA"],
        "2021-01-04",
        "2021-01-08",
        100_000.0,
        7.0,
    )
    stop_trades = [t for t in res.trades if t.reason in ("stop", "stop-gap")]
    assert stop_trades  # the stop fired
    assert stop_trades[0].price_usd == pytest.approx(90.0)  # intraday touch fills at the stop


def test_gap_through_stop_fires_before_rebalance():
    sessions = CAL.sessions("2021-01-04", "2021-01-08")
    n = len(sessions)
    opens = [100.0, 100.0, 85.0] + [100.0] * (n - 3)  # S2 opens at 85, gapping below the 90 stop
    panel = {"AAA": _bars(sessions, opens, list(opens))}
    engine = BacktestEngine(CAL, CostModel(_zero_costs()), THRESHOLDS)
    res = engine.run(
        HoldWithStop("AAA", 90.0),
        panel,
        lambda _d: ["AAA"],
        "2021-01-04",
        "2021-01-08",
        100_000.0,
        7.0,
    )
    s2_trades = [t for t in res.trades if t.date == sessions[2]]
    assert s2_trades
    # The resting stop fires at the gap-open BEFORE any fresh sizing that day.
    assert s2_trades[0].side == -1
    assert s2_trades[0].reason == "stop-gap"
    assert s2_trades[0].price_usd == pytest.approx(85.0)


class TightenStop:
    name = "tighten_stop"

    def __init__(self, symbol):
        self.symbol = symbol
        self.step = 0

    def target_positions(self, ctx):
        self.step += 1
        stop = 80.0 if self.step == 1 else 90.0  # tighten after the first decision
        return [TargetPosition(self.symbol, 1.0, stop_price=stop)]


def test_tightened_stop_applies_on_next_fill():
    sessions = CAL.sessions("2021-01-04", "2021-01-08")
    n = len(sessions)
    opens = [100.0, 100.0, 85.0] + [85.0] * (n - 3)  # S2 opens at 85
    panel = {"AAA": _bars(sessions, opens, list(opens))}
    engine = BacktestEngine(CAL, CostModel(_zero_costs()), THRESHOLDS)
    res = engine.run(
        TightenStop("AAA"), panel, lambda _d: ["AAA"], "2021-01-04", "2021-01-08", 100_000.0, 7.0
    )
    # Entry with stop 80; the next decision tightens it to 90; S2 opens at 85, gapping
    # below the NEW 90 stop, so the position must exit at the gap-open.
    stop_trades = [
        t for t in res.trades if t.date == sessions[2] and t.reason in ("stop", "stop-gap")
    ]
    assert stop_trades
    assert stop_trades[0].reason == "stop-gap"
    assert stop_trades[0].price_usd == pytest.approx(85.0)


class ChurnThenHold:
    """Buy, exit (realize a gain), re-buy fully, then hold - to leave a held position
    with low cash going into year-end."""

    name = "churn_then_hold"

    def __init__(self, symbol):
        self.symbol = symbol
        self.step = 0

    def target_positions(self, ctx):
        self.step += 1
        if self.step == 2:
            return []  # exit to realize the gain
        return [TargetPosition(self.symbol, 1.0)]


def test_tax_settlement_raises_cash_instead_of_levering():
    thresholds = {2020: 55_300.0, 2021: 79_400.0}
    sessions = CAL.sessions("2020-12-24", "2021-01-06")
    n = len(sessions)
    # buy @100, sell @200 (realize a 2020 gain), re-buy @200 and hold into 2021.
    opens = [100.0, 100.0, 200.0] + [200.0] * (n - 3)
    panel = {"AAA": _bars(sessions, opens, list(opens))}
    engine = BacktestEngine(CAL, CostModel(_zero_costs()), thresholds)
    res = engine.run(
        ChurnThenHold("AAA"),
        panel,
        lambda _d: ["AAA"],
        "2020-12-24",
        "2021-01-06",
        100_000.0,
        7.0,
    )
    # 2020 tax is due while fully invested with little cash -> shares are sold to cover it.
    assert res.tax_by_year[2020] == pytest.approx(progressive_tax(99_400.0, 55_300.0, 0.27, 0.42))
    assert any(t.reason == "tax-raise" for t in res.trades)
    assert res.equity_dkk.min() > 0  # equity never goes negative (no leverage)


def test_final_year_tax_accrues_when_fully_invested():
    # Realize a gain and stay fully invested through end (single year). The final-year
    # tax must ACCRUE against equity - not try to sell into the now-closed year (which
    # would crash) - and equity stays positive.
    sessions = CAL.sessions("2021-01-04", "2021-01-15")
    n = len(sessions)
    opens = [100.0, 100.0, 200.0] + [200.0] * (n - 3)
    panel = {"AAA": _bars(sessions, opens, list(opens))}
    engine = BacktestEngine(CAL, CostModel(_zero_costs()), THRESHOLDS)
    res = engine.run(
        ChurnThenHold("AAA"),
        panel,
        lambda _d: ["AAA"],
        "2021-01-04",
        "2021-01-15",
        100_000.0,
        7.0,
    )
    assert res.tax_by_year[2021] == pytest.approx(29_838.0)
    assert res.final_equity_dkk == pytest.approx(169_562.0, rel=1e-6)
    assert not any(t.reason == "tax-raise" for t in res.trades)  # accrued, not sold


def test_tax_raise_covers_with_commission():
    thresholds = {2020: 55_300.0, 2021: 79_400.0}
    sessions = CAL.sessions("2020-12-24", "2021-01-06")
    n = len(sessions)
    opens = [100.0, 100.0, 200.0] + [200.0] * (n - 3)
    panel = {"AAA": _bars(sessions, opens, list(opens))}
    costs = CostConfig(
        commission_rate_us=0.0008,
        min_commission_us=1.0,
        half_spread_bps=2.5,
        slippage_bps=2.5,
        fx_conversion_rate=0.0025,
        custody_fee_annual=0.0,
    )
    engine = BacktestEngine(CAL, CostModel(costs), thresholds)
    res = engine.run(
        ChurnThenHold("AAA"),
        panel,
        lambda _d: ["AAA"],
        "2020-12-24",
        "2021-01-06",
        100_000.0,
        7.0,
    )
    # The raise covers the bill despite commissions, so equity never goes negative.
    assert any(t.reason == "tax-raise" for t in res.trades)
    assert res.equity_dkk.min() > 0
