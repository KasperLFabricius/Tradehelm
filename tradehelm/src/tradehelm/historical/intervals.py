"""Supported interval utilities for historical and backtest workflows."""
from __future__ import annotations

from datetime import timedelta

SUPPORTED_INTERVALS: tuple[str, ...] = ("1min", "5min", "15min", "30min", "1h")

_INTERVAL_TO_DELTA: dict[str, timedelta] = {
    "1min": timedelta(minutes=1),
    "5min": timedelta(minutes=5),
    "15min": timedelta(minutes=15),
    "30min": timedelta(minutes=30),
    "1h": timedelta(hours=1),
}


class IntervalValidationError(ValueError):
    """Raised when an interval is not currently supported."""



def supported_intervals() -> list[str]:
    return list(SUPPORTED_INTERVALS)



def ensure_supported_interval(interval: str) -> str:
    normalized = interval.strip()
    if normalized not in _INTERVAL_TO_DELTA:
        raise IntervalValidationError(f"Unsupported interval: {interval}")
    return normalized



def interval_to_timedelta(interval: str) -> timedelta:
    normalized = ensure_supported_interval(interval)
    return _INTERVAL_TO_DELTA[normalized]
