from datetime import datetime

from tradehelm.config.models import FrictionConfig
from tradehelm.persistence.db import FillRecord, OrderRecord, PositionRecord, create_session_factory
from tradehelm.trading_engine.cost_model import GenericCostModel
from tradehelm.trading_engine.paper_broker import PaperBroker
from tradehelm.trading_engine.types import Bar, OrderSide, OrderStatus, OrderType


def test_paper_broker_partial_then_full_fill():
    sf = create_session_factory("sqlite:///:memory:")
    broker = PaperBroker(sf, GenericCostModel(FrictionConfig()))
    oid = broker.submit_order("DEMO", OrderSide.BUY, 3, OrderType.MARKET)
    bar = Bar(ts=datetime.utcnow(), symbol="DEMO", open=10, high=10, low=10, close=10, volume=1)
    broker.on_bar(bar)
    broker.on_bar(bar)
    with sf() as s:
        order = s.get(OrderRecord, oid)
        fills = s.query(FillRecord).filter(FillRecord.order_id == oid).all()
        assert order is not None
        assert order.status == OrderStatus.FILLED.value
        assert len(fills) >= 2


def test_open_position_last_price_updates_without_fill():
    sf = create_session_factory("sqlite:///:memory:")
    broker = PaperBroker(sf, GenericCostModel(FrictionConfig()))
    broker.submit_order("DEMO", OrderSide.BUY, 1, OrderType.MARKET)
    first = Bar(ts=datetime.utcnow(), symbol="DEMO", open=10, high=10, low=10, close=10, volume=1)
    broker.on_bar(first)

    second = Bar(ts=datetime.utcnow(), symbol="DEMO", open=12, high=12, low=12, close=12, volume=1)
    broker.on_bar(second)

    with sf() as s:
        pos = s.get(PositionRecord, "DEMO")
        assert pos is not None
        assert pos.last_price == broker.cost_model.round_price(12)


def test_reversal_sets_new_avg_entry_for_residual_position():
    sf = create_session_factory("sqlite:///:memory:")
    broker = PaperBroker(sf, GenericCostModel(FrictionConfig()))
    bar = Bar(ts=datetime.utcnow(), symbol="DEMO", open=10, high=10, low=10, close=10, volume=1)

    broker.submit_order("DEMO", OrderSide.BUY, 1, OrderType.MARKET)
    broker.on_bar(bar)

    broker.submit_order("DEMO", OrderSide.SELL, 3, OrderType.MARKET)
    broker.on_bar(bar)
    broker.on_bar(bar)

    with sf() as s:
        pos = s.get(PositionRecord, "DEMO")
        assert pos is not None
        assert pos.qty < 0
        assert pos.avg_entry == pos.last_price


def test_fill_fee_is_commission_only_no_spread_slippage_double_count():
    cfg = FrictionConfig(
        commission_fixed=0.0,
        commission_rate=0.001,
        minimum_commission=0.0,
        assumed_spread_bps=20.0,
        assumed_slippage_bps=20.0,
    )
    model = GenericCostModel(cfg)
    sf = create_session_factory("sqlite:///:memory:")
    broker = PaperBroker(sf, model)
    oid = broker.submit_order("DEMO", OrderSide.BUY, 1, OrderType.MARKET)
    bar = Bar(ts=datetime.utcnow(), symbol="DEMO", open=100, high=100, low=100, close=100, volume=1)
    broker.on_bar(bar)

    with sf() as s:
        fill = s.query(FillRecord).filter(FillRecord.order_id == oid).first()
        assert fill is not None
        assert fill.price > 100  # implicit impact in execution price
        assert fill.fee == model.estimate_commission(fill.price, 1)
