"""Non-secret configuration schema, loaded from config.yaml.

Every value's meaning and provenance is documented in docs/COSTS_AND_TAX.md and
docs/ARCHITECTURE.md section 5. All money values are in the currency named by the
field or its section; fractions are unit-less (0.01 == 1%); *_bps are basis
points; *_dkk are Danish kroner.

Unknown keys are rejected (extra="forbid") so a typo in config.yaml fails loudly
rather than being silently ignored.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Placeholder values that the owner must confirm before Gate 7G (CLAUDE.md rule 5).
# Dotted paths into AppConfig; later phases / the UI surface these as "unverified".
TODO_VERIFY_KEYS: tuple[str, ...] = (
    "costs.commission_rate_us",
    "costs.min_commission_us",
    "costs.fx_conversion_rate",
    "costs.custody_fee_annual",
    "tax.thresholds.2026",
)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BrokerConfig(_Base):
    """Which Saxo environment to target. Defaults to SIM (CLAUDE.md rule 2)."""

    environment: Literal["sim", "live"] = "sim"
    live: bool = False

    @property
    def is_live(self) -> bool:
        """True only when BOTH switches select live trading."""
        return self.live and self.environment == "live"


class DataConfig(_Base):
    cache_dir: str = ".cache/data"
    base_currency: str = "DKK"
    trading_currency: str = "USD"


class StorageConfig(_Base):
    db_path: str = "tradehelm.db"


class CostConfig(_Base):
    """Saxo trading costs. See docs/COSTS_AND_TAX.md section 1. TODO-VERIFY values."""

    commission_rate_us: float = 0.0008
    min_commission_us: float = 1.00
    half_spread_bps: float = 2.5
    slippage_bps: float = 2.5
    fx_conversion_rate: float = 0.0025
    custody_fee_annual: float = 0.0


class TaxConfig(_Base):
    """Danish aktieindkomst model. See docs/COSTS_AND_TAX.md section 2.

    thresholds maps a calendar year to the DKK progression point at which the
    marginal rate steps from rate_low to rate_high.
    """

    rate_low: float = 0.27
    rate_high: float = 0.42
    thresholds: dict[int, float] = Field(default_factory=dict)


class RiskConfig(_Base):
    """Risk limits, identical in backtest and live. See ARCHITECTURE.md section 5."""

    max_positions: int = 3
    per_position_risk_frac: float = 0.01
    max_position_notional_frac: float = 0.40
    max_daily_loss_frac: float = 0.02
    max_drawdown_frac: float = 0.10
    price_collar_frac: float = 0.05
    min_ticket_dkk: float = 2000.0


class AppConfig(_Base):
    """Root of the non-secret configuration tree."""

    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    costs: CostConfig = Field(default_factory=CostConfig)
    tax: TaxConfig = Field(default_factory=TaxConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
