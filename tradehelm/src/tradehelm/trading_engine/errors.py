"""Domain-specific errors for API-safe handling."""
from __future__ import annotations


class EngineError(Exception):
    code = "engine_error"


class InvalidTransitionError(EngineError):
    code = "invalid_mode_transition"


class ReplayNotLoadedError(EngineError):
    code = "replay_not_loaded"


class InvalidReplayPathError(EngineError):
    code = "invalid_replay_path"


class StrategyNotFoundError(EngineError):
    code = "strategy_not_found"
