import time
from datetime import datetime

from tradehelm.config.models import AppConfig, FrictionConfig, RiskConfig
from tradehelm.persistence.db import OrderRecord, PositionRecord, create_session_factory
from tradehelm.strategies.noop import NoOpStrategy
from tradehelm.trading_engine.engine import TradingEngine
from tradehelm.trading_engine.types import Bar, BotMode, OrderSide, OrderType


def _csv(tmp_path):
    p = tmp_path / "bars.csv"
    p.write_text(
        "timestamp,symbol,open,high,low,close,volume\n"
        "2026-01-01T14:30:00Z,DEMO,100,101,99,100,1000\n"
        "2026-01-01T14:31:00Z,DEMO,100,101,99,101,1000\n"
        "2026-01-01T14:32:00Z,DEMO,101,102,100,102,1000\n"
    )
    return str(p)


def test_apply_config_updates_runtime_components():
    sf = create_session_factory("sqlite:///:memory:")
    engine = TradingEngine(sf, AppConfig(), [NoOpStrategy()])
    new_cfg = AppConfig(
        replay_speed=12,
        friction=FrictionConfig(tick_size=0.05),
        risk=RiskConfig(max_trades_per_day=1),
    )
    engine.apply_config(new_cfg)
    assert engine.market_data.replay_speed == 12
    assert engine.cost_model.config.tick_size == 0.05
    assert engine.risk.config.max_trades_per_day == 1


def test_replay_start_non_blocking_and_stop(tmp_path):
    sf = create_session_factory("sqlite:///:memory:")
    cfg = AppConfig(replay_speed=50)
    engine = TradingEngine(sf, cfg, [NoOpStrategy()])
    engine.load_replay(_csv(tmp_path))
    engine.set_mode(BotMode.OBSERVE)

    start = time.time()
    result = engine.start_replay()
    elapsed = time.time() - start
    assert result["started"] is True
    assert elapsed < 0.2
    assert engine.replay_running is True

    stop_res = engine.stop_replay()
    assert stop_res["stop_requested"] is True
    for _ in range(30):
        if not engine.replay_running:
            break
        time.sleep(0.02)
    assert engine.replay_running is False


def test_kill_switch_flattens_and_cancels():
    sf = create_session_factory("sqlite:///:memory:")
    engine = TradingEngine(sf, AppConfig(), [NoOpStrategy()])
    oid = engine.broker.submit_order("DEMO", OrderSide.BUY, 2, OrderType.MARKET)
    bar = Bar(ts=datetime.utcnow(), symbol="DEMO", open=10, high=11, low=9, close=10, volume=10)
    engine.broker.on_bar(bar)
    engine.set_mode(BotMode.KILL_SWITCH)

    with sf() as s:
        order = s.get(OrderRecord, oid)
        pos = s.get(PositionRecord, "DEMO")
        assert order is not None
        assert order.status in {"CANCELLED", "FILLED"}
        assert pos is not None
        assert pos.qty == 0


def test_cooldown_activates_after_close():
    sf = create_session_factory("sqlite:///:memory:")
    engine = TradingEngine(sf, AppConfig(), [NoOpStrategy()])
    engine.broker.submit_order("DEMO", OrderSide.BUY, 1, OrderType.MARKET)
    bar = Bar(ts=datetime.utcnow(), symbol="DEMO", open=100, high=100, low=100, close=100, volume=1)
    engine.broker.on_bar(bar)
    engine.broker.submit_order("DEMO", OrderSide.SELL, 1, OrderType.MARKET)
    engine.broker.on_bar(bar)

    ok, reason = engine.risk.validate("DEMO", 1, 100, estimated_edge=1.0, daily_pnl=0, current_positions=0)
    assert not ok
    assert "cooldown" in reason
