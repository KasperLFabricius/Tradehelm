"""Secret configuration, loaded from environment variables / .env.

Secrets are NEVER read from config.yaml and NEVER committed. All are optional so
that Phase 0 and CI run without any credentials; a phase that needs a given
secret validates its presence at that point.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Secrets(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TRADEHELM_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    saxo_app_key: str | None = None
    saxo_app_secret: str | None = None
    anthropic_api_key: str | None = None
    api_token: str | None = None
