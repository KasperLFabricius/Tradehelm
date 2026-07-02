"""Cost-model tests (docs/COSTS_AND_TAX.md section 1)."""

import pytest

from tradehelm.backtest import CostModel
from tradehelm.config import CostConfig


def _cfg(**kw):
    base = {
        "commission_rate_us": 0.0008,
        "min_commission_us": 1.0,
        "half_spread_bps": 2.5,
        "slippage_bps": 2.5,
        "fx_conversion_rate": 0.0025,
        "custody_fee_annual": 0.0,
    }
    base.update(kw)
    return CostConfig(**base)


def test_fill_price_adds_spread_and_slippage():
    m = CostModel(_cfg())  # 2.5 + 2.5 = 5 bps = 0.0005
    assert m.fill_price(100.0, side=1) == pytest.approx(100.05)  # buy pays up
    assert m.fill_price(100.0, side=-1) == pytest.approx(99.95)  # sell receives less


def test_commission_uses_minimum_then_rate():
    m = CostModel(_cfg())
    assert m.commission(100.0) == pytest.approx(1.0)  # 0.08% of 100 = 0.08 < 1.0 floor
    assert m.commission(100_000.0) == pytest.approx(80.0)  # 0.0008 * 100000


def test_fx_fee_on_funding_amount():
    m = CostModel(_cfg())
    assert m.fx_fee(10_000.0) == pytest.approx(25.0)  # 0.25%


def test_invalid_side_rejected():
    m = CostModel(_cfg())
    with pytest.raises(ValueError):
        m.fill_price(100.0, side=0)
