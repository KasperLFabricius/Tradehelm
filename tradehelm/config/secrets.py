"""Secret configuration, loaded from environment variables / .env.

Secrets are NEVER read from config.yaml and NEVER committed. All are optional so
that Phase 0 and CI run without any credentials; a phase that needs a given
secret validates its presence at that point.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor the default .env to the repo root (two levels up from this file), the
# same way config.yaml is resolved. A relative ".env" would be looked up in the
# process CWD, so launching from e.g. a Task Scheduler entry with no "Start in"
# directory would silently miss the repo .env and read every secret as None.
DEFAULT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRADEHELM_",
        env_file=DEFAULT_ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    saxo_app_key: str | None = None
    saxo_app_secret: str | None = None
    anthropic_api_key: str | None = None
    api_token: str | None = None

    @field_validator("*", mode="before")
    @classmethod
    def _blank_to_none(cls, value: Any) -> Any:
        """Treat a blank/whitespace env var as absent.

        A user copying .env.example leaves optional Phase 5/6 secrets as e.g.
        `TRADEHELM_API_TOKEN=`; pydantic-settings would otherwise read that as
        "" rather than None, so an `is None` presence check would treat a
        missing secret as configured. Normalise blanks to None.
        """
        if isinstance(value, str) and value.strip() == "":
            return None
        return value
