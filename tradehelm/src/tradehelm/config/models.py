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


class OrbStrategyConfig(BaseModel):
    enabled: bool = True
    qty: int = Field(default=10, ge=1)
    opening_range_bars: int = Field(default=3, ge=1, le=20)
    breakout_buffer: float = Field(default=0.05, ge=0)
    direction: str = Field(default="BOTH", pattern="^(LONG|SHORT|BOTH)$")
    stop_loss: float = Field(default=0.4, gt=0)
    take_profit: float = Field(default=0.8, gt=0)
    max_bars_in_trade: int = Field(default=12, ge=1)
    flatten_end_of_session: bool = True


class VwapStrategyConfig(BaseModel):
    enabled: bool = True
    qty: int = Field(default=10, ge=1)
    pullback_threshold: float = Field(default=0.15, gt=0)
    reentry_buffer: float = Field(default=0.05, ge=0)
    direction: str = Field(default="BOTH", pattern="^(LONG|SHORT|BOTH)$")
    stop_loss: float = Field(default=0.35, gt=0)
    take_profit: float = Field(default=0.7, gt=0)
    max_bars_in_trade: int = Field(default=10, ge=1)


class StrategiesConfig(BaseModel):
    orb: OrbStrategyConfig = OrbStrategyConfig()
    vwap: VwapStrategyConfig = VwapStrategyConfig()


class HistoricalProviderConfig(BaseModel):
    name: str = "twelvedata"
    api_key_env: str = "TWELVE_DATA_API_KEY"
    cache_dir: str = "./historical_cache"
    default_adjusted: bool = True


class AppConfig(BaseModel):
    replay_speed: float = Field(default=1.0, ge=0.1, le=50)
    friction: FrictionConfig = FrictionConfig()
    risk: RiskConfig = RiskConfig()
    strategies: StrategiesConfig = StrategiesConfig()
    historical: HistoricalProviderConfig = HistoricalProviderConfig()
