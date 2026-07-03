"""Configuration loading tests.

Acceptance for Phase 0: config.load() round-trips a sample config (docs/PLAN.md).
"""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from tradehelm import config
from tradehelm.config import (
    MODELING_ASSUMPTIONS,
    TODO_VERIFY_KEYS,
    AppConfig,
    CostConfig,
    Secrets,
    Settings,
    default_config_path,
    default_env_path,
    dump_app_config,
    load,
    load_app_config,
)

# A minimal but complete set of the required financial sections, for tests that
# construct an AppConfig directly.
_MIN_FINANCIAL = {
    "costs": {
        "commission_rate_us": 0.0008,
        "min_commission_us": 1.0,
        "half_spread_bps": 2.5,
        "slippage_bps": 2.5,
        "fx_conversion_rate": 0.0025,
        "custody_fee_annual": 0.0,
    },
    "tax": {"rate_low": 0.27, "rate_high": 0.42, "thresholds": {2026: 79400}},
    "risk": {
        "max_positions": 3,
        "per_position_risk_frac": 0.01,
        "max_position_notional_frac": 0.40,
        "max_daily_loss_frac": 0.02,
        "max_drawdown_frac": 0.10,
        "price_collar_frac": 0.05,
        "min_ticket_dkk": 2000.0,
    },
}


def test_sample_config_loads_expected_values():
    cfg = load_app_config(default_config_path())
    assert cfg.broker.environment == "sim"
    assert cfg.broker.live is False
    assert cfg.data.trading_currency == "USD"
    assert cfg.costs.commission_rate_us == pytest.approx(0.0008)
    assert cfg.tax.rate_low == pytest.approx(0.27)
    assert cfg.tax.rate_high == pytest.approx(0.42)
    # thresholds keys parse as ints, values as numbers
    assert cfg.tax.thresholds[2026] == pytest.approx(79400)
    assert cfg.risk.max_positions == 3


def test_app_config_round_trips_through_yaml():
    original = load_app_config(default_config_path())
    text = dump_app_config(original)
    reparsed = AppConfig.model_validate(yaml.safe_load(text))
    assert reparsed == original


def test_load_returns_settings_with_app_and_secrets():
    settings = load(default_config_path(), env_file=None)
    assert isinstance(settings, Settings)
    assert isinstance(settings.app, AppConfig)
    assert isinstance(settings.secrets, Secrets)
    assert settings.source_path == default_config_path()
    assert settings.app.broker.environment == "sim"


def test_app_config_requires_financial_sections():
    # costs/tax/risk have no defaults: an all-default AppConfig is impossible.
    with pytest.raises(ValidationError):
        AppConfig()
    # thresholds must define at least one year.
    fin = {**_MIN_FINANCIAL, "tax": {"rate_low": 0.27, "rate_high": 0.42, "thresholds": {}}}
    with pytest.raises(ValidationError, match="thresholds"):
        AppConfig.model_validate(fin)


def test_operational_sections_default_when_financial_present():
    cfg = AppConfig.model_validate(_MIN_FINANCIAL)
    assert cfg.broker.environment == "sim"
    assert cfg.broker.is_live is False
    assert cfg.data.cache_dir == ".cache/data"
    assert cfg.storage.db_path == "tradehelm.db"
    assert cfg.risk.per_position_risk_frac == pytest.approx(0.01)


def test_unknown_key_is_rejected():
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"unknown_section": {}})
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"broker": {"environment": "sim", "typo": 1}})


def test_is_live_requires_both_switches():
    assert config.BrokerConfig(environment="sim", live=False).is_live is False
    assert config.BrokerConfig(environment="live", live=False).is_live is False
    assert config.BrokerConfig(environment="sim", live=True).is_live is False
    assert config.BrokerConfig(environment="live", live=True).is_live is True


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_app_config(tmp_path / "does_not_exist.yaml")


def test_empty_config_file_raises(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_app_config(empty)


def test_non_mapping_config_file_raises(tmp_path):
    scalar = tmp_path / "scalar.yaml"
    scalar.write_text("just-a-string", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping"):
        load_app_config(scalar)


def test_empty_mapping_config_raises(tmp_path):
    # {} is a valid mapping but omits the required financial sections.
    f = tmp_path / "braces.yaml"
    f.write_text("{}", encoding="utf-8")
    with pytest.raises(ValidationError, match="Field required"):
        load_app_config(f)


def test_config_missing_financial_section_raises(tmp_path):
    f = tmp_path / "partial.yaml"
    # Full costs + tax but omits risk (broker/data/storage may be omitted).
    f.write_text(
        "costs:\n"
        "  commission_rate_us: 0.0008\n"
        "  min_commission_us: 1.0\n"
        "  half_spread_bps: 2.5\n"
        "  slippage_bps: 2.5\n"
        "  fx_conversion_rate: 0.0025\n"
        "  custody_fee_annual: 0.0\n"
        "tax: {rate_low: 0.27, rate_high: 0.42, thresholds: {2026: 79400}}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="risk"):
        load_app_config(f)


def test_config_partial_financial_section_raises(tmp_path):
    # A non-empty but PARTIAL costs block must be rejected (missing fields
    # would otherwise silently default). This is Codex round-5's case.
    f = tmp_path / "partialcosts.yaml"
    f.write_text(
        "costs: {commission_rate_us: 0.0008}\n"
        "tax: {rate_low: 0.27, rate_high: 0.42, thresholds: {2026: 79400}}\n"
        "risk: {max_positions: 3, per_position_risk_frac: 0.01,"
        " max_position_notional_frac: 0.4, max_daily_loss_frac: 0.02,"
        " max_drawdown_frac: 0.1, price_collar_frac: 0.05, min_ticket_dkk: 2000}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="min_commission_us"):
        load_app_config(f)


def test_config_empty_financial_section_raises(tmp_path):
    f = tmp_path / "emptysec.yaml"
    # costs present but empty must be rejected too.
    f.write_text(
        "costs: {}\n"
        "tax: {rate_low: 0.27, rate_high: 0.42, thresholds: {2026: 79400}}\n"
        "risk: {max_positions: 3, per_position_risk_frac: 0.01,"
        " max_position_notional_frac: 0.4, max_daily_loss_frac: 0.02,"
        " max_drawdown_frac: 0.1, price_collar_frac: 0.05, min_ticket_dkk: 2000}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="commission_rate_us"):
        load_app_config(f)


def test_secrets_read_from_environment(monkeypatch):
    monkeypatch.setenv("TRADEHELM_SAXO_APP_KEY", "sim-key-123")
    secrets = Secrets(_env_file=None)
    assert secrets.saxo_app_key == "sim-key-123"


def test_secrets_default_to_none(monkeypatch):
    for name in ("SAXO_APP_KEY", "SAXO_APP_SECRET", "ANTHROPIC_API_KEY", "API_TOKEN"):
        monkeypatch.delenv(f"TRADEHELM_{name}", raising=False)
    secrets = Secrets(_env_file=None)
    assert secrets.saxo_app_key is None
    assert secrets.anthropic_api_key is None


def test_empty_process_env_does_not_mask_dotenv(monkeypatch, tmp_path):
    # An empty process-level var must not shadow a real value in the .env file.
    envfile = tmp_path / ".env"
    envfile.write_text("TRADEHELM_SAXO_APP_KEY=real-key\n", encoding="utf-8")
    monkeypatch.setenv("TRADEHELM_SAXO_APP_KEY", "")
    secrets = Secrets(_env_file=str(envfile))
    assert secrets.saxo_app_key == "real-key"


def test_secrets_blank_env_var_is_none(monkeypatch):
    # A .env copied from .env.example leaves optional secrets blank; an empty
    # (or whitespace-only) env var must normalise to None, not "".
    monkeypatch.setenv("TRADEHELM_API_TOKEN", "")
    monkeypatch.setenv("TRADEHELM_ANTHROPIC_API_KEY", "   ")
    secrets = Secrets(_env_file=None)
    assert secrets.api_token is None
    assert secrets.anthropic_api_key is None


def test_default_env_path_is_absolute_and_at_repo_root():
    env_path = default_env_path()
    assert env_path.is_absolute()
    assert env_path.name == ".env"
    # Same repo root as config.yaml, so both resolve independently of the CWD.
    assert env_path.parent == default_config_path().parent


def test_secrets_env_file_is_repo_root_anchored():
    env_file = Secrets.model_config.get("env_file")
    assert env_file is not None
    assert Path(env_file).is_absolute()
    assert Path(env_file) == default_env_path()


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("costs", "slippage_bps", -2.5),
        ("costs", "half_spread_bps", -1.0),
        ("costs", "fx_conversion_rate", -0.0025),
        ("costs", "commission_rate_us", 1.5),  # >100% rate
        ("tax", "rate_high", -0.42),
        ("risk", "max_daily_loss_frac", -0.02),
        ("risk", "max_positions", 0),
    ],
)
def test_out_of_range_money_values_rejected(section, field, value):
    bad = {**_MIN_FINANCIAL, section: {**_MIN_FINANCIAL[section], field: value}}
    with pytest.raises(ValidationError):
        AppConfig.model_validate(bad)


def test_negative_tax_threshold_rejected():
    bad = {**_MIN_FINANCIAL, "tax": {**_MIN_FINANCIAL["tax"], "thresholds": {2026: -1}}}
    with pytest.raises(ValidationError, match="non-negative"):
        AppConfig.model_validate(bad)


def test_todo_verify_keys_present():
    assert TODO_VERIFY_KEYS
    assert "tax.thresholds.2026" in TODO_VERIFY_KEYS
    # The approximate historical thresholds are also tracked as unverified.
    assert "tax.thresholds.2005" in TODO_VERIFY_KEYS
    assert "tax.thresholds.2023" in TODO_VERIFY_KEYS


def test_every_cost_field_is_classified():
    # Each cost field is either a price-list TODO-VERIFY item or a stress-tested
    # modeling assumption - exactly one, never neither. Adding a new cost field
    # without classifying it fails this test.
    cost_fields = {f"costs.{name}" for name in CostConfig.model_fields}
    todo_cost_keys = {k for k in TODO_VERIFY_KEYS if k.startswith("costs.")}
    assert set(MODELING_ASSUMPTIONS).isdisjoint(todo_cost_keys)
    assert todo_cost_keys | set(MODELING_ASSUMPTIONS) == cost_fields
