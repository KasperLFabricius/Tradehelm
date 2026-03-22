from datetime import datetime

from sqlalchemy import select

from tradehelm.config.models import AppConfig, FrictionConfig, RiskConfig
from tradehelm.persistence.db import ClosedTradeRecord, FillRecord, OrderRecord, PositionRecord, create_session_factory
from tradehelm.providers.interfaces import Strategy
from tradehelm.trading_engine.engine import TradingEngine
from tradehelm.trading_engine.types import Bar, OrderSide, OrderType


class SequenceStrategy(Strategy):
    strategy_id = "seq"

    def __init__(self, intents_by_ts: dict[str, list[dict]]) -> None:
        self.intents_by_ts = intents_by_ts

    def on_bar(self, bar: Bar) -> list[dict]:
        return self.intents_by_ts.get(bar.ts.isoformat(), [])


class MultiIntentStrategy(Strategy):
    strategy_id = "multi"

    def on_bar(self, bar: Bar) -> list[dict]:
        return [
            {"symbol": "AAA", "side": OrderSide.BUY, "qty": 1},
            {"symbol": "BBB", "side": OrderSide.BUY, "qty": 1},
        ]


def test_apply_config_updates_runtime_components():
    sf = create_session_factory("sqlite:///:memory:")
    engine = TradingEngine(sf, AppConfig(), [])
    new_cfg = AppConfig(
        replay_speed=12,
        friction=FrictionConfig(tick_size=0.05),
        risk=RiskConfig(max_trades_per_day=1),
    )
    engine.apply_config(new_cfg)
    assert engine.market_data.replay_speed == 12
    assert engine.cost_model.config.tick_size == 0.05
    assert engine.risk.config.max_trades_per_day == 1


def test_max_trades_per_day_resets_on_day_change():
    sf = create_session_factory("sqlite:///:memory:")
    cfg = AppConfig(risk=RiskConfig(max_trades_per_day=1))
    day1 = datetime.fromisoformat("2026-01-01T14:30:00+00:00")
    day2 = datetime.fromisoformat("2026-01-02T14:30:00+00:00")
    strategy = SequenceStrategy(
        {
            day1.isoformat(): [{"symbol": "DEMO", "side": OrderSide.BUY, "qty": 1}],
            day2.isoformat(): [{"symbol": "DEMO", "side": OrderSide.BUY, "qty": 1}],
        }
    )
    engine = TradingEngine(sf, cfg, [strategy])

    engine._roll_day_if_needed(day1)
    engine._trade_bar(Bar(day1, "DEMO", 100, 100, 100, 100, 1))
    assert engine.risk.trades_today == 1

    engine._trade_bar(Bar(day1.replace(minute=31), "DEMO", 100, 100, 100, 100, 1))
    with sf() as s:
        assert len(s.scalars(select(OrderRecord)).all()) == 1

    engine._roll_day_if_needed(day2)
    assert engine.risk.trades_today == 0
    engine._trade_bar(Bar(day2, "DEMO", 100, 100, 100, 100, 1))
    with sf() as s:
        assert len(s.scalars(select(OrderRecord)).all()) == 2


def test_max_daily_loss_is_day_scoped_not_cumulative():
    sf = create_session_factory("sqlite:///:memory:")
    cfg = AppConfig(risk=RiskConfig(max_daily_loss=50, max_trades_per_day=10))
    ts1 = datetime.fromisoformat("2026-01-01T14:30:00+00:00")
    ts2 = datetime.fromisoformat("2026-01-02T14:30:00+00:00")
    strategy = SequenceStrategy({ts1.isoformat(): [{"symbol": "DEMO", "side": OrderSide.BUY, "qty": 1}], ts2.isoformat(): [{"symbol": "DEMO", "side": OrderSide.BUY, "qty": 1}]})
    engine = TradingEngine(sf, cfg, [strategy])

    engine._roll_day_if_needed(ts1)
    engine.day_realized_pnl = -60.0
    engine._trade_bar(Bar(ts1, "DEMO", 100, 100, 100, 100, 1))
    with sf() as s:
        assert len(s.scalars(select(OrderRecord)).all()) == 0

    engine._roll_day_if_needed(ts2)
    assert engine.day_realized_pnl == 0.0
    engine._trade_bar(Bar(ts2, "DEMO", 100, 100, 100, 100, 1))
    with sf() as s:
        assert len(s.scalars(select(OrderRecord)).all()) == 1


def test_projected_position_count_rejects_second_intent_same_bar():
    sf = create_session_factory("sqlite:///:memory:")
    cfg = AppConfig(risk=RiskConfig(max_simultaneous_positions=1, max_trades_per_day=10))
    engine = TradingEngine(sf, cfg, [MultiIntentStrategy()])
    bar = Bar(datetime.fromisoformat("2026-01-01T14:30:00+00:00"), "AAA", 10, 10, 10, 10, 1)
    engine._roll_day_if_needed(bar.ts)
    engine._trade_bar(bar)
    with sf() as s:
        orders = s.scalars(select(OrderRecord)).all()
        assert len(orders) == 1
        assert orders[0].symbol == "AAA"


def test_intent_symbol_used_for_cooldown_validation():
    sf = create_session_factory("sqlite:///:memory:")
    cfg = AppConfig(risk=RiskConfig(max_trades_per_day=10))
    ts = datetime.fromisoformat("2026-01-01T14:30:00+00:00")
    strategy = SequenceStrategy({ts.isoformat(): [{"symbol": "BBB", "side": OrderSide.BUY, "qty": 1}]})
    engine = TradingEngine(sf, cfg, [strategy])
    engine.risk.on_exit("BBB")
    engine._roll_day_if_needed(ts)
    engine._trade_bar(Bar(ts, "AAA", 10, 10, 10, 10, 1))
    with sf() as s:
        assert len(s.scalars(select(OrderRecord)).all()) == 0


def test_kill_switch_flatten_creates_accounting_artifacts():
    sf = create_session_factory("sqlite:///:memory:")
    engine = TradingEngine(sf, AppConfig(), [])
    ts = datetime.fromisoformat("2026-01-01T14:30:00+00:00")
    engine._roll_day_if_needed(ts)
    engine.broker.submit_order("DEMO", OrderSide.BUY, 1, OrderType.MARKET)
    engine.broker.on_bar(Bar(ts, "DEMO", 100, 100, 100, 100, 1))

    before = engine.day_realized_pnl
    engine.kill_switch_flatten()

    with sf() as s:
        pos = s.get(PositionRecord, "DEMO")
        fills = s.scalars(select(FillRecord).where(FillRecord.symbol == "DEMO")).all()
        trades = s.scalars(select(ClosedTradeRecord).where(ClosedTradeRecord.symbol == "DEMO")).all()
        assert pos is not None and pos.qty == 0 and pos.avg_entry == 0.0
        assert len(fills) >= 2
        assert len(trades) >= 1
    assert engine.day_realized_pnl != before
