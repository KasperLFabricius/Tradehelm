"""Configuration models for TradeHelm."""
from pydantic import BaseModel, Field


class FrictionConfig(BaseModel):
    commission_fixed: float = 0.5
    commission_rate: float = 0.0005
    minimum_commission: float = 1.0
    assumed_spread_bps: float = 2.0
    assumed_slippage_bps: float = 3.0
    tick_size: float = 0.01


class RiskConfig(BaseModel):
    max_daily_loss: float = 500.0
    max_risk_per_trade: float = 150.0
    max_simultaneous_positions: int = 5
    max_position_size: int = 1000
    max_trades_per_day: int = 20
    cooldown_bars_after_exit: int = 5


class AppConfig(BaseModel):
    replay_speed: float = Field(default=1.0, ge=0.1, le=50)
    friction: FrictionConfig = FrictionConfig()
    risk: RiskConfig = RiskConfig()
