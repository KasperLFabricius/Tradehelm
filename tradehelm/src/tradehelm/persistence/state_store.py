"""Persistence helpers for config and runtime metadata."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel
from sqlalchemy.orm import sessionmaker

from tradehelm.config.models import AppConfig
from tradehelm.persistence.db import AppConfigRecord, RuntimeMetadataRecord
from tradehelm.trading_engine.types import BotMode


class RuntimeMetadata(BaseModel):
    replay_path: str | None = None
    replay_speed: float | None = None
    last_mode: BotMode | None = None

    @classmethod
    def from_engine_state(cls, replay_path: str | None, replay_speed: float, last_mode: BotMode) -> "RuntimeMetadata":
        return cls(replay_path=replay_path, replay_speed=replay_speed, last_mode=last_mode)

    def resolved_replay_path(self) -> str | None:
        if self.replay_path is None:
            return None
        return str(Path(self.replay_path).expanduser().resolve())


class PersistedStateStore:
    """Stores and restores runtime config + safe metadata."""

    def __init__(self, session_factory: sessionmaker) -> None:
        self.session_factory = session_factory

    def load_or_init_config(self, default_config: AppConfig) -> AppConfig:
        with self.session_factory() as session:
            row = session.get(AppConfigRecord, 1)
            if row is None:
                row = AppConfigRecord(id=1, version="v1", payload_json=default_config.model_dump_json())
                session.add(row)
                session.commit()
                return default_config
            return AppConfig.model_validate_json(row.payload_json)

    def save_config(self, config: AppConfig) -> None:
        with self.session_factory() as session:
            row = session.get(AppConfigRecord, 1)
            if row is None:
                row = AppConfigRecord(id=1)
                session.add(row)
            row.version = "v1"
            row.payload_json = config.model_dump_json()
            row.updated_at = datetime.now(timezone.utc)
            session.commit()

    def load_metadata(self) -> dict[str, str | float | None]:
        with self.session_factory() as session:
            row = session.get(RuntimeMetadataRecord, 1)
            if row is None:
                return {}
            return RuntimeMetadata.model_validate_json(row.payload_json).model_dump()

    def save_metadata(self, metadata: dict[str, str | float | None]) -> None:
        normalized = RuntimeMetadata.model_validate(metadata)
        with self.session_factory() as session:
            row = session.get(RuntimeMetadataRecord, 1)
            if row is None:
                row = RuntimeMetadataRecord(id=1)
                session.add(row)
            row.payload_json = normalized.model_dump_json()
            row.updated_at = datetime.now(timezone.utc)
            session.commit()
