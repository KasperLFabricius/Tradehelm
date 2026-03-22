from tradehelm.config.models import FrictionConfig
from tradehelm.trading_engine.cost_model import GenericCostModel
from tradehelm.trading_engine.types import OrderSide


def test_cost_model_round_trip_positive_and_tick_rounding():
    model = GenericCostModel(FrictionConfig(tick_size=0.05))
    assert model.round_price(100.023) == 100.0
    assert model.estimate_round_trip_cost(100, 10) > 0


def test_market_fill_prices_are_directionally_worse():
    model = GenericCostModel(FrictionConfig(assumed_spread_bps=2.0, assumed_slippage_bps=3.0))
    ref = 100.0
    buy_px = model.adjusted_fill_price(ref, OrderSide.BUY)
    sell_px = model.adjusted_fill_price(ref, OrderSide.SELL)
    assert buy_px > ref
    assert sell_px < ref


def test_explicit_fee_is_commission_only():
    model = GenericCostModel(FrictionConfig(commission_fixed=0.5, commission_rate=0.001, minimum_commission=1.0))
    price, qty = 100.0, 2
    commission = model.estimate_commission(price, qty)
    explicit = model.estimate_one_way_explicit_cost(price, qty)
    assert explicit == commission


def test_round_trip_cost_includes_explicit_and_implicit_components():
    cfg = FrictionConfig(
        commission_fixed=0.5,
        commission_rate=0.001,
        minimum_commission=1.0,
        assumed_spread_bps=4.0,
        assumed_slippage_bps=2.0,
    )
    model = GenericCostModel(cfg)
    price, qty = 100.0, 10
    expected = 2 * (
        model.estimate_one_way_explicit_cost(price, qty)
        + model.estimate_one_way_implicit_cost(price, qty)
    )
    assert model.estimate_round_trip_cost(price, qty) == expected
