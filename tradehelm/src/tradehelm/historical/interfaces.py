"""Historical provider interfaces and typed payloads."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime

from tradehelm.trading_engine.types import Bar


SUPPORTED_INTERVAL = "5min"


@dataclass(slots=True)
class SplitEvent:
    symbol: str
    ex_date: date
    ratio_from: float
    ratio_to: float


@dataclass(slots=True)
class DividendEvent:
    symbol: str
    ex_date: date
    amount: float


class HistoricalDataProvider(ABC):
    """Abstract provider for historical bars and corporate actions."""

    name: str

    @abstractmethod
    def fetch_bars(self, symbol: str, interval: str, start_date: date, end_date: date) -> list[Bar]:
        """Fetch raw intraday bars in UTC."""

    @abstractmethod
    def fetch_splits(self, symbol: str, start_date: date, end_date: date) -> list[SplitEvent]:
        """Fetch split events for the requested date range."""

    @abstractmethod
    def fetch_dividends(self, symbol: str, start_date: date, end_date: date) -> list[DividendEvent]:
        """Fetch dividend events for the requested date range."""


@dataclass(slots=True)
class FetchWindow:
    start: datetime
    end: datetime
