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
from .secrets import DEFAULT_ENV_FILE, Secrets

__all__ = [
    "REQUIRED_SECTIONS",
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
    "default_env_path",
    "dump_app_config",
    "load",
    "load_app_config",
]

DEFAULT_CONFIG_FILENAME = "config.yaml"
CONFIG_ENV_VAR = "TRADEHELM_CONFIG"

# Sections that must be present and non-empty in a loaded config file. They carry
# the money-consequential / TODO-VERIFY values; silently defaulting them would
# defeat the fail-loud guarantee (CLAUDE.md rule 5).
REQUIRED_SECTIONS: tuple[str, ...] = ("costs", "tax", "risk")

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


def default_env_path() -> Path:
    """Repo-root .env (anchored via __file__, independent of the process CWD)."""
    return DEFAULT_ENV_FILE


def _resolve_path(path: str | os.PathLike[str] | None) -> Path:
    if path is not None:
        return Path(path)
    env = os.environ.get(CONFIG_ENV_VAR)
    if env:
        return Path(env)
    return default_config_path()


def load_app_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load and validate the non-secret configuration from YAML.

    Fail-loud contract (we never silently trade on unconfirmed defaults):
    - missing file            -> FileNotFoundError
    - blank / null / scalar   -> ValueError
    - missing/empty costs, tax or risk section -> ValueError
    - unknown or malformed key -> ValidationError

    broker/data/storage may be omitted: their defaults are operational and safe
    (broker defaults to SIM), whereas costs/tax/risk carry the money-consequential,
    TODO-VERIFY values that must be present explicitly.
    """
    resolved = _resolve_path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"Config file not found: {resolved}")
    with resolved.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)
    if raw is None:
        raise ValueError(f"Config file is empty: {resolved}")
    if not isinstance(raw, dict):
        raise ValueError(
            f"Config file must be a YAML mapping, got {type(raw).__name__}: {resolved}"
        )
    missing = [section for section in REQUIRED_SECTIONS if not raw.get(section)]
    if missing:
        raise ValueError(
            f"Config file {resolved} is missing or has empty required section(s): "
            f"{', '.join(missing)}. These carry cost/tax/risk values that must be set "
            "explicitly, not filled from placeholder defaults."
        )
    return AppConfig.model_validate(raw)


def dump_app_config(cfg: AppConfig) -> str:
    """Serialize an AppConfig back to YAML. Round-trips through load_app_config."""
    return yaml.safe_dump(cfg.model_dump(), sort_keys=False, allow_unicode=False)


def load(
    path: str | os.PathLike[str] | None = None,
    *,
    env_file: str | os.PathLike[str] | None = DEFAULT_ENV_FILE,
) -> Settings:
    """Load app config from YAML and secrets from the environment / .env.

    env_file defaults to the repo-root .env (CWD-independent). Pass env_file=None
    to read secrets from OS environment variables only (no dotenv file).
    """
    resolved = _resolve_path(path)
    app = load_app_config(resolved)
    secrets = Secrets(_env_file=env_file)  # type: ignore[call-arg]
    return Settings(app=app, secrets=secrets, source_path=resolved)
