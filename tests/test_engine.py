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
