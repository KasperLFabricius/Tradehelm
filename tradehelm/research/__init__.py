"""Research harness (Phase 3b): walk-forward parameter study, append-only trials
ledger, deflated Sharpe, and the REPORT.md renderer. No live-trading or LLM code
here; it only reads a supplied price panel and runs the backtest engine offline.
See docs/STRATEGY_SPEC.md and docs/PLAN.md Phase 3.
"""

from __future__ import annotations

from .report import build_report
from .study import (
    PARAM_GRIDS,
    CandidateStudy,
    WindowOOS,
    doubled_costs,
    make_candidate,
    run_candidate_study,
    summarize_returns,
)
from .trials import Trial, TrialLog, params_str

__all__ = [
    "PARAM_GRIDS",
    "CandidateStudy",
    "Trial",
    "TrialLog",
    "WindowOOS",
    "build_report",
    "doubled_costs",
    "make_candidate",
    "params_str",
    "run_candidate_study",
    "summarize_returns",
]
