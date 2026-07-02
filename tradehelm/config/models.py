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

    commission_rate_us: float
    min_commission_us: float
    half_spread_bps: float
    slippage_bps: float
    fx_conversion_rate: float
    custody_fee_annual: float


class TaxConfig(_Base):
    """Danish aktieindkomst model. See docs/COSTS_AND_TAX.md section 2.

    All fields REQUIRED. thresholds maps a calendar year to the DKK progression
    point at which the marginal rate steps from rate_low to rate_high, and must
    define at least one year.
    """

    rate_low: float
    rate_high: float
    thresholds: dict[int, float]

    @field_validator("thresholds")
    @classmethod
    def _thresholds_non_empty(cls, value: dict[int, float]) -> dict[int, float]:
        if not value:
            raise ValueError("tax.thresholds must define at least one year")
        return value


class RiskConfig(_Base):
    """Risk limits, identical in backtest and live. See ARCHITECTURE.md section 5.

    All fields REQUIRED (limits must be set explicitly, not defaulted).
    """

    max_positions: int
    per_position_risk_frac: float
    max_position_notional_frac: float
    max_daily_loss_frac: float
    max_drawdown_frac: float
    price_collar_frac: float
    min_ticket_dkk: float


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
