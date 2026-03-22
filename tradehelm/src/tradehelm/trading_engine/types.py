"""Core domain types for TradeHelm."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class BotMode(str, Enum):
    """Supported operator-facing bot modes."""

    STOPPED = "STOPPED"
    OBSERVE = "OBSERVE"
    PAPER = "PAPER"
    HALTED = "HALTED"
    KILL_SWITCH = "KILL_SWITCH"


class OrderSide(str, Enum):
    """Order side enumeration."""

    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Order type enumeration."""

    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    """Order life-cycle status."""

    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


class StrategyAction(str, Enum):
    """Supported deterministic strategy actions."""

    ENTRY = "ENTRY"
    EXIT = "EXIT"


@dataclass(slots=True)
class Bar:
    """Represents one replay bar for a symbol."""

    ts: datetime
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(slots=True)
class StrategyIntent:
    """Typed strategy intent produced by deterministic strategy logic."""

    symbol: str
    side: OrderSide
    qty: int
    action: StrategyAction
    strategy_id: str
    reason: str = ""
    metadata: dict[str, float | int | str | bool] = field(default_factory=dict)
