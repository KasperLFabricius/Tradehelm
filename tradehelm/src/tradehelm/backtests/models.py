"""Backtest request/override models."""
from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from tradehelm.historical.interfaces import DEFAULT_INTERVAL


class BacktestRequest(BaseModel):
    symbols: list[str]
    start_date: date
    end_date: date
    interval: str = DEFAULT_INTERVAL
    adjusted: bool = True
    enabled_strategies: list[str] | None = None
    strategy_params: dict[str, dict] = Field(default_factory=dict)
    friction_overrides: dict | None = None
    risk_overrides: dict | None = None


class BacktestCompareRequest(BaseModel):
    run_ids: list[int]
