"""Advisor (Claude API pre-market / post-close briefings). Implemented in Phase 6.

ISOLATION RULE (CLAUDE.md rule 1): this is the only package allowed to call an
LLM. Nothing in the trade path (strategy, risk, backtest, engine, broker) may
import this package; it may be imported only by api and the scheduler. A
dependency-inspection test enforces this once there is code to inspect.
"""
