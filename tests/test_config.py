"""Configuration loading tests.

Acceptance for Phase 0: config.load() round-trips a sample config (docs/PLAN.md).
"""

import pytest
import yaml
from pydantic import ValidationError

from tradehelm import config
from tradehelm.config import (
    TODO_VERIFY_KEYS,
    AppConfig,
    Secrets,
    Settings,
    default_config_path,
    dump_app_config,
    load,
    load_app_config,
)


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


def test_defaults_are_sane_without_a_file():
    cfg = AppConfig()
    assert cfg.broker.environment == "sim"
    assert cfg.broker.is_live is False
    assert cfg.risk.per_position_risk_frac == pytest.approx(0.01)
    assert cfg.tax.thresholds == {}


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


def test_secrets_blank_env_var_is_none(monkeypatch):
    # A .env copied from .env.example leaves optional secrets blank; an empty
    # (or whitespace-only) env var must normalise to None, not "".
    monkeypatch.setenv("TRADEHELM_API_TOKEN", "")
    monkeypatch.setenv("TRADEHELM_ANTHROPIC_API_KEY", "   ")
    secrets = Secrets(_env_file=None)
    assert secrets.api_token is None
    assert secrets.anthropic_api_key is None


def test_todo_verify_keys_present():
    assert TODO_VERIFY_KEYS
    assert "tax.thresholds.2026" in TODO_VERIFY_KEYS
