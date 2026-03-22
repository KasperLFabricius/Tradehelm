"""Deterministic analyzer/observer-style artifacts for backtest runs."""
from __future__ import annotations

from collections import defaultdict


class RunAnalysisService:
    def _normalize_action(self, raw_action: object) -> str:
        if hasattr(raw_action, "value"):
            raw_action = getattr(raw_action, "value")
        return str(raw_action or "UNKNOWN").strip().upper()

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
            action = self._normalize_action(decision.get("action"))
            if accepted and action == "ENTRY":
                accepted_entries_by_strategy[strategy_id] += 1
        return {
            "by_reason": dict(sorted(by_reason.items(), key=lambda item: item[0])),
            "by_acceptance": by_acceptance,
            "decision_count_by_strategy": dict(sorted(by_strategy.items(), key=lambda item: item[0])),
            "trade_count_by_strategy": dict(sorted(accepted_entries_by_strategy.items(), key=lambda item: item[0])),
            "total_decisions": len(decisions),
        }

    def build_strategy_summary(self, decisions: list[dict], trades: list[dict]) -> list[dict]:
        stats: dict[str, dict] = defaultdict(lambda: {"accepted_entries": 0, "accepted_exits": 0, "rejected_entries": 0, "rejected_exits": 0, "closed_trades": 0, "net_pnl": 0.0})
        for decision in decisions:
            sid = str(decision.get("strategy_id") or "unknown")
            accepted = bool(decision.get("accepted"))
            action = self._normalize_action(decision.get("action"))
            key = ("accepted" if accepted else "rejected") + ("_entries" if action == "ENTRY" else "_exits")
            if key in stats[sid]:
                stats[sid][key] += 1
        # Best-effort attribution: use entry strategy id when present on trade payload.
        for trade in trades:
            sid = str(trade.get("strategy_id") or "unknown")
            stats[sid]["closed_trades"] += 1
            stats[sid]["net_pnl"] += float(trade.get("net_pnl", 0.0) or 0.0)
        return [{"strategy_id": sid, **values} for sid, values in sorted(stats.items(), key=lambda item: item[0])]

    def build_trade_timeline(self, trades: list[dict]) -> list[dict]:
        ordered = sorted(trades, key=lambda t: (t.get("exit_ts") or t.get("entry_ts") or "", t.get("id") or 0))
        return [
            {
                "trade_id": trade.get("id"),
                "symbol": trade.get("symbol"),
                "side": trade.get("side"),
                "entry_ts": trade.get("entry_ts"),
                "exit_ts": trade.get("exit_ts"),
                "qty": trade.get("qty"),
                "net_pnl": float(trade.get("net_pnl", 0.0) or 0.0),
                "fees": float(trade.get("fees", 0.0) or 0.0),
            }
            for trade in ordered
        ]

    def build_headline_summary(self, summary: dict, equity_curve: list[dict]) -> dict:
        wins = int(summary.get("winning_trades", 0) or 0)
        losses = int(summary.get("losing_trades", 0) or 0)
        trade_count = int(summary.get("total_closed_trades", 0) or 0)
        gross_profit = float(summary.get("gross_profit", 0.0) or 0.0)
        gross_loss = abs(float(summary.get("gross_loss", 0.0) or 0.0))
        avg_winner = (gross_profit / wins) if wins else 0.0
        avg_loser = -(gross_loss / losses) if losses else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else None
        expectancy = float(summary.get("net_realized_pnl", 0.0) or 0.0) / trade_count if trade_count else 0.0
        max_drawdown = 0.0
        peak = 0.0
        for point in equity_curve:
            equity = float(point.get("equity", 0.0) or 0.0)
            peak = max(peak, equity)
            max_drawdown = max(max_drawdown, peak - equity)
        total_days = max(float(summary.get("active_session_days", 1.0) or 1.0), 1.0)
        return {
            "net_pnl": float(summary.get("net_realized_pnl", 0.0) or 0.0),
            "gross_pnl": float(summary.get("gross_realized_pnl", 0.0) or 0.0),
            "win_rate": float(summary.get("win_rate", 0.0) or 0.0),
            "trade_count": trade_count,
            "total_fees": float(summary.get("total_fees_paid", 0.0) or 0.0),
            "best_trade": float(summary.get("best_trade", 0.0) or 0.0),
            "worst_trade": float(summary.get("worst_trade", 0.0) or 0.0),
            "average_winner": avg_winner,
            "average_loser": avg_loser,
            "expectancy_per_trade": expectancy,
            "trades_per_day": trade_count / total_days,
            "profit_factor": profit_factor,
            "max_drawdown": max_drawdown,
        }

    def build_run_artifacts(self, trades: list[dict], decisions: list[dict], summary: dict | None = None) -> dict:
        equity_curve = self.build_equity_curve(trades)
        return {
            "equity_curve": equity_curve,
            "symbol_summary": self.build_symbol_summary(trades),
            "decision_summary": self.build_decision_summary(decisions),
            "strategy_summary": self.build_strategy_summary(decisions, trades),
            "trade_timeline": self.build_trade_timeline(trades),
            "headline_summary": self.build_headline_summary(summary or {}, equity_curve),
        }
