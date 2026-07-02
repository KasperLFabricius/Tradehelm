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

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Placeholder values that the owner must confirm before Gate 7G (CLAUDE.md rule 5).
# Dotted paths into AppConfig; later phases / the UI surface these as "unverified".
TODO_VERIFY_KEYS: tuple[str, ...] = (
    "costs.commission_rate_us",
    "costs.min_commission_us",
    "costs.fx_conversion_rate",
    "costs.custody_fee_annual",
    "tax.thresholds.2026",
)

# Cost inputs that are modeling ASSUMPTIONS (execution quality), not figures to
# confirm against the Saxo price list. Per docs/COSTS_AND_TAX.md the research
# protocol stress-tests these (costs x2) rather than "verifying" them, so they
# are tracked separately and must never be mistaken for confirmed price-list
# values or, conversely, dropped as if already confirmed.
MODELING_ASSUMPTIONS: tuple[str, ...] = (
    "costs.half_spread_bps",
    "costs.slippage_bps",
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
    """Saxo trading costs. See docs/COSTS_AND_TAX.md section 1.

    All fields are REQUIRED (no defaults) and money-consequential, so they must
    be set explicitly in config.yaml (CLAUDE.md rule 5). Two categories:
    - price-list / revisor values to confirm before Gate 7G (TODO_VERIFY_KEYS):
      commission_rate_us, min_commission_us, fx_conversion_rate, custody_fee_annual;
    - execution assumptions the research protocol stress-tests, not verified
      against a price list (MODELING_ASSUMPTIONS): half_spread_bps, slippage_bps.
    """

    # All non-negative; the fraction rates are additionally capped at 1.0 (100%)
    # so a gross typo cannot silently pass this fail-loud guard.
    commission_rate_us: float = Field(ge=0.0, le=1.0)
    min_commission_us: float = Field(ge=0.0)
    half_spread_bps: float = Field(ge=0.0)
    slippage_bps: float = Field(ge=0.0)
    fx_conversion_rate: float = Field(ge=0.0, le=1.0)
    custody_fee_annual: float = Field(ge=0.0, le=1.0)


class TaxConfig(_Base):
    """Danish aktieindkomst model. See docs/COSTS_AND_TAX.md section 2.

    All fields REQUIRED. thresholds maps a calendar year to the DKK progression
    point at which the marginal rate steps from rate_low to rate_high, and must
    define at least one year.
    """

    rate_low: float = Field(ge=0.0, le=1.0)
    rate_high: float = Field(ge=0.0, le=1.0)
    thresholds: dict[int, float]

    @field_validator("thresholds")
    @classmethod
    def _validate_thresholds(cls, value: dict[int, float]) -> dict[int, float]:
        if not value:
            raise ValueError("tax.thresholds must define at least one year")
        if any(amount < 0 for amount in value.values()):
            raise ValueError("tax.thresholds amounts must be non-negative")
        return value


class RiskConfig(_Base):
    """Risk limits, identical in backtest and live. See ARCHITECTURE.md section 5.

    All fields REQUIRED (limits must be set explicitly, not defaulted).
    """

    max_positions: int = Field(ge=1)
    per_position_risk_frac: float = Field(gt=0.0, le=1.0)
    max_position_notional_frac: float = Field(gt=0.0, le=1.0)
    max_daily_loss_frac: float = Field(gt=0.0, le=1.0)
    max_drawdown_frac: float = Field(gt=0.0, le=1.0)
    price_collar_frac: float = Field(gt=0.0, le=1.0)
    min_ticket_dkk: float = Field(ge=0.0)


class AppConfig(_Base):
    """Root of the non-secret configuration tree.

    broker/data/storage may be omitted (their defaults are operational and safe;
    broker defaults to SIM). costs/tax/risk are REQUIRED - omitting any of them,
    or any field within them, fails validation, so a truncated config never runs
    on placeholder cost/tax/risk values.
    """

    broker: BrokerConfig = Field(default_factory=BrokerConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    costs: CostConfig
    tax: TaxConfig
    risk: RiskConfig
