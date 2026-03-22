from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from tradehelm.config.models import AppConfig, OrbStrategyConfig, VwapStrategyConfig
from tradehelm.persistence.db import DecisionRecord, OrderRecord, create_session_factory
from tradehelm.strategies.orb import OpeningRangeBreakoutStrategy
from tradehelm.strategies.vwap import VwapContinuationStrategy
from tradehelm.trading_engine.engine import TradingEngine
from tradehelm.trading_engine.types import Bar, OrderSide, StrategyAction, StrategyIntent


def _bar(ts: datetime, close: float, symbol: str = "AAA") -> Bar:
    return Bar(ts=ts, symbol=symbol, open=close, high=close + 0.05, low=close - 0.05, close=close, volume=100)


def test_orb_no_duplicate_entries_after_breakout():
    cfg = OrbStrategyConfig(opening_range_bars=2, breakout_buffer=0.0, qty=1)
    s = OpeningRangeBreakoutStrategy(cfg)
    t0 = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
    bars = [_bar(t0, 100), _bar(t0 + timedelta(minutes=1), 100.1), _bar(t0 + timedelta(minutes=2), 101), _bar(t0 + timedelta(minutes=3), 101.2)]
    intents = [i for b in bars for i in s.on_bar(b)]
    assert len([i for i in intents if i.action == StrategyAction.ENTRY]) == 1


def test_orb_direction_control():
    t0 = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
    long_only = OpeningRangeBreakoutStrategy(OrbStrategyConfig(direction="LONG", opening_range_bars=2, breakout_buffer=0.0, qty=1))
    short_only = OpeningRangeBreakoutStrategy(OrbStrategyConfig(direction="SHORT", opening_range_bars=2, breakout_buffer=0.0, qty=1))

    short_break = [_bar(t0, 100), _bar(t0 + timedelta(minutes=1), 100.2), _bar(t0 + timedelta(minutes=2), 99.0)]
    assert long_only.on_bar(short_break[0]) == []
    assert long_only.on_bar(short_break[1]) == []
    assert long_only.on_bar(short_break[2]) == []

    long_break = [_bar(t0, 100), _bar(t0 + timedelta(minutes=1), 99.8), _bar(t0 + timedelta(minutes=2), 101.0)]
    assert short_only.on_bar(long_break[0]) == []
    assert short_only.on_bar(long_break[1]) == []
    assert short_only.on_bar(long_break[2]) == []


def test_orb_exits_stop_target_and_max_bars():
    t0 = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)

    stop_s = OpeningRangeBreakoutStrategy(OrbStrategyConfig(opening_range_bars=2, breakout_buffer=0.0, stop_loss=0.2, take_profit=2, max_bars_in_trade=5, qty=1))
    stop_s.on_bar(_bar(t0, 100))
    stop_s.on_bar(_bar(t0 + timedelta(minutes=1), 100))
    stop_s.on_bar(_bar(t0 + timedelta(minutes=2), 101))
    stop_bar = Bar(t0 + timedelta(minutes=3), "AAA", 100.9, 101.0, 100.6, 100.8, 100)
    stop_intents = stop_s.on_bar(stop_bar)
    assert stop_intents and stop_intents[0].reason == "orb_stop_exit"

    target_s = OpeningRangeBreakoutStrategy(OrbStrategyConfig(opening_range_bars=2, breakout_buffer=0.0, stop_loss=2, take_profit=0.2, max_bars_in_trade=5, qty=1))
    target_s.on_bar(_bar(t0, 100))
    target_s.on_bar(_bar(t0 + timedelta(minutes=1), 100))
    target_s.on_bar(_bar(t0 + timedelta(minutes=2), 101))
    target_bar = Bar(t0 + timedelta(minutes=3), "AAA", 101.1, 101.3, 101.0, 101.2, 100)
    target_intents = target_s.on_bar(target_bar)
    assert target_intents and target_intents[0].reason == "orb_target_exit"

    max_s = OpeningRangeBreakoutStrategy(OrbStrategyConfig(opening_range_bars=2, breakout_buffer=0.0, stop_loss=2, take_profit=2, max_bars_in_trade=1, qty=1))
    max_s.on_bar(_bar(t0, 100))
    max_s.on_bar(_bar(t0 + timedelta(minutes=1), 100))
    max_s.on_bar(_bar(t0 + timedelta(minutes=2), 101))
    max_intents = max_s.on_bar(_bar(t0 + timedelta(minutes=3), 101.05))
    assert max_intents and max_intents[0].reason == "orb_max_bars_exit"


def test_strategy_state_resets_new_session():
    s = OpeningRangeBreakoutStrategy(OrbStrategyConfig(opening_range_bars=2, breakout_buffer=0.0, qty=1))
    d1 = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
    d2 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)

    s.on_bar(_bar(d1, 100))
    s.on_bar(_bar(d1 + timedelta(minutes=1), 100))
    assert s.on_bar(_bar(d1 + timedelta(minutes=2), 101))

    assert s.on_bar(_bar(d2, 100)) == []
    assert s.on_bar(_bar(d2 + timedelta(minutes=1), 100)) == []
    day2_entry = s.on_bar(_bar(d2 + timedelta(minutes=2), 101))
    assert day2_entry and day2_entry[0].reason == "orb_breakout_long"


def test_vwap_continuation_entry_sequence():
    s = VwapContinuationStrategy(VwapStrategyConfig(pullback_threshold=0.08, reentry_buffer=0.05, qty=1))
    t0 = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
    bars = [_bar(t0, 100), _bar(t0 + timedelta(minutes=1), 100.2), _bar(t0 + timedelta(minutes=2), 100.4)]
    for b in bars:
        assert s.on_bar(b) == []
    pullback = Bar(t0 + timedelta(minutes=3), "AAA", 100.3, 100.35, 100.1, 100.2, 100)
    assert s.on_bar(pullback) == []
    entry_bar = Bar(t0 + timedelta(minutes=4), "AAA", 100.35, 100.45, 100.3, 100.4, 100)
    intents = s.on_bar(entry_bar)
    assert intents and intents[0].reason == "vwap_pullback_entry"


def test_engine_handles_typed_intents_and_reasons_persisted():
    class TypedIntentStrategy:
        strategy_id = "typed"

        def on_bar(self, bar: Bar):
            return [
                StrategyIntent(symbol=bar.symbol, side=OrderSide.BUY, qty=1, action=StrategyAction.ENTRY, strategy_id="typed", reason="typed_entry")
            ]

        def status(self) -> dict:
            return {}

    sf = create_session_factory("sqlite:///:memory:")
    engine = TradingEngine(sf, AppConfig(), [TypedIntentStrategy()])
    ts = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
    engine._roll_day_if_needed(ts)
    engine._trade_bar(_bar(ts, 100))

    with sf() as s:
        orders = s.scalars(select(OrderRecord)).all()
        decisions = s.scalars(select(DecisionRecord)).all()
        assert len(orders) == 1
        assert decisions[0].reason == "typed_entry"
