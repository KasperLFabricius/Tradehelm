"""Strategy protocol and deterministic candidates A/B/C plus the buy-and-hold
benchmark. Implemented in Phase 3. No LLM/API-judgment calls here (CLAUDE.md rule
1). See docs/STRATEGY_SPEC.md.
"""

from __future__ import annotations

from ..backtest.engine import Strategy, StrategyContext, TargetPosition
from . import indicators
from .base import PositionBook, RiskParams
from .benchmark import BuyAndHold
from .candidates import CandidateA, CandidateB, CandidateC

__all__ = [
    "BuyAndHold",
    "CandidateA",
    "CandidateB",
    "CandidateC",
    "PositionBook",
    "RiskParams",
    "Strategy",
    "StrategyContext",
    "TargetPosition",
    "indicators",
]
