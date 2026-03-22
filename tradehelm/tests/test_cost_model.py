from tradehelm.config.models import FrictionConfig
from tradehelm.trading_engine.cost_model import GenericCostModel


def test_cost_model_round_trip_positive_and_tick_rounding():
    model = GenericCostModel(FrictionConfig(tick_size=0.05))
    assert model.round_price(100.023) == 100.0
    assert model.estimate_round_trip_cost(100, 10) > 0
