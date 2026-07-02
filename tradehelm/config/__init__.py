"""Configuration loading for Tradehelm.

Public API:
    load(path=None, env_file=".env") -> Settings   # app config + secrets
    load_app_config(path=None) -> AppConfig         # non-secret config only
    dump_app_config(cfg) -> str                     # AppConfig -> YAML (round-trip)
    default_config_path() -> Path                    # repo-root config.yaml

The non-secret config comes from a YAML file (default: config.yaml at the repo
root, overridable by the TRADEHELM_CONFIG env var or an explicit path). Secrets
come from the environment / .env. The two are never mixed in one file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .models import (
    TODO_VERIFY_KEYS,
    AppConfig,
    BrokerConfig,
    CostConfig,
    DataConfig,
    RiskConfig,
    StorageConfig,
    TaxConfig,
)
from .secrets import Secrets

__all__ = [
    "TODO_VERIFY_KEYS",
    "AppConfig",
    "BrokerConfig",
    "CostConfig",
    "DataConfig",
    "RiskConfig",
    "Secrets",
    "Settings",
    "StorageConfig",
    "TaxConfig",
    "default_config_path",
    "dump_app_config",
    "load",
    "load_app_config",
]

DEFAULT_CONFIG_FILENAME = "config.yaml"
CONFIG_ENV_VAR = "TRADEHELM_CONFIG"

# Repo root = two levels up from this file (tradehelm/config/__init__.py).
_REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """The fully resolved configuration: non-secret app config plus secrets."""

    app: AppConfig
    secrets: Secrets
    source_path: Path


def default_config_path() -> Path:
    """Repo-root config.yaml (the checked-in sample / default configuration)."""
    return _REPO_ROOT / DEFAULT_CONFIG_FILENAME


def _resolve_path(path: str | os.PathLike[str] | None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get(CONFIG_ENV_VAR)
    if env:
        return Path(env)
    return default_config_path()


def load_app_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load and validate the non-secret configuration from YAML.

    Raises FileNotFoundError if the file is missing (we never silently trade on
    default costs/tax) and ValidationError on unknown or malformed keys.
    """
    resolved = _resolve_path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"Config file not found: {resolved}")
    with resolved.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return AppConfig.model_validate(raw)


def dump_app_config(cfg: AppConfig) -> str:
    """Serialize an AppConfig back to YAML. Round-trips through load_app_config."""
    return yaml.safe_dump(cfg.model_dump(), sort_keys=False, allow_unicode=False)


def load(
    path: str | os.PathLike[str] | None = None,
    *,
    env_file: str | os.PathLike[str] | None = ".env",
) -> Settings:
    """Load app config from YAML and secrets from the environment / .env."""
    resolved = _resolve_path(path)
    app = load_app_config(resolved)
    secrets = Secrets(_env_file=env_file)  # type: ignore[call-arg]
    return Settings(app=app, secrets=secrets, source_path=resolved)
