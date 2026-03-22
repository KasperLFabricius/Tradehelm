from tradehelm.trading_engine.state_machine import BotStateMachine
from tradehelm.trading_engine.types import BotMode


def test_state_transitions_and_kill_switch_lock():
    sm = BotStateMachine()
    assert sm.mode == BotMode.STOPPED
    sm.set_mode(BotMode.OBSERVE)
    assert sm.mode == BotMode.OBSERVE
    sm.set_mode(BotMode.KILL_SWITCH)
    assert sm.mode == BotMode.KILL_SWITCH
    try:
        sm.set_mode(BotMode.PAPER)
        assert False
    except ValueError:
        assert True
