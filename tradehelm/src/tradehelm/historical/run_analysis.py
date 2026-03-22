"""Deterministic analyzer/observer-style artifacts for backtest runs."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime


class RunAnalysisService:
    def build_equity_curve(self, trades: list[dict]) -> list[dict]:
        ordered = sorted(trades, key=lambda t: (t.get("exit_ts") or "", t.get("id") or 0))
        curve: list[dict] = []
        equity = 0.0
        for trade in ordered:
            net_pnl = float(trade.get("net_pnl", 0.0) or 0.0)
            equity += net_pnl
            curve.append(
                {
                    "timestamp": trade.get("exit_ts") or trade.get("entry_ts"),
                    "equity": equity,
                    "realized_pnl": equity,
                    "unrealized_pnl": 0.0,
                }
            )
        if not curve:
            curve.append(
                {
                    "timestamp": None,
                    "equity": 0.0,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0,
                }
            )
        return curve

    def build_symbol_summary(self, trades: list[dict]) -> list[dict]:
        grouped: dict[str, dict] = defaultdict(lambda: {"trades": 0, "net_pnl": 0.0, "wins": 0, "fees": 0.0})
        for trade in trades:
            symbol = str(trade.get("symbol") or "UNKNOWN")
            entry = grouped[symbol]
            entry["trades"] += 1
            net_pnl = float(trade.get("net_pnl", 0.0) or 0.0)
            entry["net_pnl"] += net_pnl
            if net_pnl > 0:
                entry["wins"] += 1
            entry["fees"] += float(trade.get("fees", 0.0) or 0.0)
        out: list[dict] = []
        for symbol in sorted(grouped):
            item = grouped[symbol]
            trades_count = int(item["trades"])
            out.append(
                {
                    "symbol": symbol,
                    "trades": trades_count,
                    "net_pnl": float(item["net_pnl"]),
                    "win_rate": (float(item["wins"]) / trades_count) if trades_count else 0.0,
                    "total_fees": float(item["fees"]),
                }
            )
        return out

    def build_decision_summary(self, decisions: list[dict]) -> dict:
        by_reason: dict[str, int] = defaultdict(int)
        by_acceptance: dict[str, int] = {"accepted": 0, "rejected": 0}
        by_strategy: dict[str, int] = defaultdict(int)
        accepted_entries_by_strategy: dict[str, int] = defaultdict(int)
        for decision in decisions:
            reason = str(decision.get("reason") or "unknown")
            by_reason[reason] += 1
            accepted = bool(decision.get("accepted"))
            by_acceptance["accepted" if accepted else "rejected"] += 1
            strategy_id = str(decision.get("strategy_id") or "unknown")
            by_strategy[strategy_id] += 1
            if accepted:
                accepted_entries_by_strategy[strategy_id] += 1
        return {
            "by_reason": dict(sorted(by_reason.items(), key=lambda item: item[0])),
            "by_acceptance": by_acceptance,
            "decision_count_by_strategy": dict(sorted(by_strategy.items(), key=lambda item: item[0])),
            "trade_count_by_strategy": dict(sorted(accepted_entries_by_strategy.items(), key=lambda item: item[0])),
            "total_decisions": len(decisions),
        }

    def build_run_artifacts(self, trades: list[dict], decisions: list[dict]) -> dict:
        return {
            "equity_curve": self.build_equity_curve(trades),
            "symbol_summary": self.build_symbol_summary(trades),
            "decision_summary": self.build_decision_summary(decisions),
        }
