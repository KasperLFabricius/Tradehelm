from tradehelm.config.models import FrictionConfig
from tradehelm.persistence.db import FillRecord, OrderRecord, create_session_factory
from tradehelm.trading_engine.cost_model import GenericCostModel
from tradehelm.trading_engine.paper_broker import PaperBroker
from tradehelm.trading_engine.types import Bar, OrderSide, OrderStatus, OrderType


def test_paper_broker_partial_then_full_fill():
    sf = create_session_factory("sqlite:///:memory:")
    broker = PaperBroker(sf, GenericCostModel(FrictionConfig()))
    oid = broker.submit_order("DEMO", OrderSide.BUY, 3, OrderType.MARKET)
    bar = Bar(ts=__import__("datetime").datetime.utcnow(), symbol="DEMO", open=10, high=10, low=10, close=10, volume=1)
    broker.on_bar(bar)
    broker.on_bar(bar)
    with sf() as s:
        order = s.get(OrderRecord, oid)
        fills = s.query(FillRecord).filter(FillRecord.order_id == oid).all()
        assert order is not None
        assert order.status == OrderStatus.FILLED.value
        assert len(fills) >= 2
