"""Bot state machine with explicit transitions."""
from tradehelm.trading_engine.types import BotMode


class BotStateMachine:
    """Deterministic bot mode transition manager."""

    def __init__(self) -> None:
        self.mode = BotMode.STOPPED

    def set_mode(self, mode: BotMode) -> BotMode:
        """Set mode with kill switch handling."""
        if self.mode == BotMode.KILL_SWITCH and mode != BotMode.STOPPED:
            raise ValueError("KILL_SWITCH requires STOPPED reset")
        self.mode = mode
        return self.mode
