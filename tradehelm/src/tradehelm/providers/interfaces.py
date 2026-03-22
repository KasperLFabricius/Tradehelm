"""Provider and strategy interfaces."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from tradehelm.trading_engine.types import Bar, OrderSide, OrderType


class BrokerProvider(ABC):
    """Broker abstraction used by the trading engine."""

    @abstractmethod
    def submit_order(self, symbol: str, side: OrderSide, qty: int, order_type: OrderType, limit_price: float | None = None) -> str:
        """Submit an order and return broker order id."""

    @abstractmethod
    def cancel_order(self, order_id: str) -> None:
        """Cancel working order by id."""

    @abstractmethod
    def on_bar(self, bar: Bar) -> None:
        """Advance broker simulation to latest market bar."""


class MarketDataProvider(ABC):
    """Market data provider abstraction."""

    @abstractmethod
    def load(self, path: str) -> None:
        """Load source data."""

    @abstractmethod
    def bars(self) -> Iterable[Bar]:
        """Yield bars in chronological order."""


class Strategy(ABC):
    """Trading strategy abstraction."""

    strategy_id: str

    @abstractmethod
    def on_bar(self, bar: Bar) -> list[dict]:
        """Return list of intent dicts with symbol/side/qty and optional limit_price."""


class CostModelProvider(ABC):
    """Cost model abstraction."""

    @abstractmethod
    def estimate_round_trip_cost(self, price: float, qty: int) -> float:
        """Estimate expected round-trip monetary friction."""


class NewsProvider(ABC):
    """Stub extension point for later premium news integrations."""

    @abstractmethod
    def sentiment(self, symbol: str) -> float:
        """Return sentiment score."""


class AIScorer(ABC):
    """Stub extension point for later AI scoring module."""

    @abstractmethod
    def score(self, features: dict) -> float:
        """Return AI confidence score."""
