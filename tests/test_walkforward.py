"""Walk-forward window generation + runner smoke test."""

import pandas as pd
import pytest

from tradehelm.backtest import (
    CostModel,
    TargetPosition,
    Window,
    holdout_range,
    run_walk_forward,
    walk_forward_windows,
)
from tradehelm.config import CostConfig
from tradehelm.data import TradingCalendar


def test_windows_use_trading_day_purge():
    cal = TradingCalendar()
    windows = walk_forward_windows(
        "2010-01-01",
        "2015-01-01",
        train_years=3,
        test_years=1,
        purge_sessions=5,
        holdout_years=0,
        calendar=cal,
    )
    assert windows[0].train_start == pd.Timestamp("2010-01-01")
    assert windows[0].train_end == pd.Timestamp("2013-01-01")
    # 5 trading sessions (2013-01-02,03,04,07,08) are purged; test starts on the next.
    assert windows[0].test_start == pd.Timestamp("2013-01-09")
    purged = cal.sessions(windows[0].train_end, windows[0].test_start)
    purged = purged[(purged > windows[0].train_end) & (purged < windows[0].test_start)]
    assert len(purged) == 5  # exactly 5 trading days of purge, regardless of weekends
    assert windows[1].train_start == pd.Timestamp("2011-01-01")


def test_holdout_is_reserved_by_default():
    cal = TradingCalendar()
    windows = walk_forward_windows(
        "2010-01-01", "2020-01-01", train_years=3, test_years=1, calendar=cal
    )
    walk_end = pd.Timestamp("2018-01-01")  # 2020 - 2y
    assert windows
    for w in windows:
        assert w.test_end <= walk_end
    hold_start, hold_end = holdout_range("2020-01-01")
    assert hold_start == walk_end
    assert hold_end == pd.Timestamp("2020-01-01")


def test_invalid_years_rejected():
    with pytest.raises(ValueError):
        walk_forward_windows("2010-01-01", "2015-01-01", train_years=0)


class BuyAndHold:
    name = "bh"

    def __init__(self, symbol):
        self.symbol = symbol

    def target_positions(self, ctx):
        return [TargetPosition(self.symbol, 1.0)]


def test_run_walk_forward_smoke():
    cal = TradingCalendar()
    sessions = cal.sessions("2021-01-04", "2021-01-15")
    n = len(sessions)
    panel = {
        "AAA": pd.DataFrame(
            {
                "open": [100.0] * n,
                "high": [100.0] * n,
                "low": [100.0] * n,
                "close": [100.0] * n,
                "adj_close": [100.0] * n,
                "volume": [1] * n,
            },
            index=sessions,
        )
    }
    costs = CostModel(
        CostConfig(
            commission_rate_us=0.0,
            min_commission_us=0.0,
            half_spread_bps=0.0,
            slippage_bps=0.0,
            fx_conversion_rate=0.0,
            custody_fee_annual=0.0,
        )
    )
    window = Window(sessions[0], sessions[0], sessions[0], sessions[-1])
    results = run_walk_forward(
        [window],
        lambda _ts, _te: BuyAndHold("AAA"),
        panel,
        lambda _d: ["AAA"],
        cal,
        costs,
        {2021: 79_400.0},
        100_000.0,
        7.0,
    )
    assert len(results) == 1
    _w, res = results[0]
    assert res.final_equity_dkk == pytest.approx(100_000.0, rel=1e-6)  # flat prices, no costs
