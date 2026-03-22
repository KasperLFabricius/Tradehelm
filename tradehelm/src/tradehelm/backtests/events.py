"""Backtest event helpers."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

from tradehelm.persistence.db import BacktestEventRecord


class BacktestEventService:
    def __init__(self, session_factory: sessionmaker) -> None:
        self.session_factory = session_factory

    def add(self, job_id: int, event_type: str, message: str, run_id: int | None = None, payload: dict | None = None) -> None:
        with self.session_factory() as session:
            session.add(
                BacktestEventRecord(
                    job_id=job_id,
                    run_id=run_id,
                    event_type=event_type,
                    message=message,
                    payload_json=json.dumps(payload or {}),
                    created_at=datetime.now(timezone.utc),
                )
            )
            session.commit()

    def list_for_job(self, job_id: int) -> list[dict]:
        with self.session_factory() as session:
            rows = session.scalars(select(BacktestEventRecord).where(BacktestEventRecord.job_id == job_id).order_by(desc(BacktestEventRecord.id))).all()
            return [
                {
                    "id": row.id,
                    "job_id": row.job_id,
                    "run_id": row.run_id,
                    "event_type": row.event_type,
                    "message": row.message,
                    "payload": json.loads(row.payload_json or "{}"),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]
