"""Read-only analytics derived from persisted paper-trading records."""
from __future__ import annotations

from datetime import datetime
from statistics import mean

from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

from tradehelm.persistence.db import ClosedTradeRecord, DecisionRecord, FillRecord, ReplaySessionRecord


class AnalyticsService:
    """Compute replay summary and review payloads from persisted records."""

    def __init__(self, session_factory: sessionmaker):
        self.session_factory = session_factory

    def _holding_minutes(self, entry_ts: datetime | None, exit_ts: datetime | None) -> float | None:
        if entry_ts is None or exit_ts is None:
            return None
        return max(0.0, (exit_ts - entry_ts).total_seconds() / 60.0)

    def trades(self) -> list[dict]:
        with self.session_factory() as s:
            rows = s.scalars(select(ClosedTradeRecord).order_by(desc(ClosedTradeRecord.id))).all()
            payload: list[dict] = []
            for trade in rows:
                payload.append(
                    {
                        "id": trade.id,
                        "symbol": trade.symbol,
                        "side": trade.side,
                        "qty": trade.qty,
                        "entry_price": trade.entry_price,
                        "exit_price": trade.exit_price,
                        "entry_ts": trade.entry_ts.isoformat() if trade.entry_ts else None,
                        "exit_ts": trade.exit_ts.isoformat() if trade.exit_ts else None,
                        "holding_minutes": self._holding_minutes(trade.entry_ts, trade.exit_ts),
                        "gross_pnl": trade.gross_pnl,
                        "fees": trade.fees,
                        "net_pnl": trade.net_pnl,
                    }
                )
            return payload

    def sessions(self) -> list[dict]:
        with self.session_factory() as s:
            rows = s.scalars(select(ReplaySessionRecord).order_by(desc(ReplaySessionRecord.id))).all()
            return [
                {
                    "id": row.id,
                    "dataset": row.dataset,
                    "loaded_at": row.loaded_at.isoformat() if row.loaded_at else None,
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                    "status": row.status,
                }
                for row in rows
            ]

    def fees(self) -> dict:
        with self.session_factory() as s:
            fills = s.scalars(select(FillRecord)).all()
        total_fees = float(sum(fill.fee for fill in fills))
        return {
            "total_explicit_fees": total_fees,
            "fill_count": len(fills),
        }

    def summary(self) -> dict:
        trades = self.trades()
        total = len(trades)
        net_values = [float(t["net_pnl"]) for t in trades]
        gross_values = [float(t["gross_pnl"]) for t in trades]
        winners = len([v for v in net_values if v > 0])
        losers = len([v for v in net_values if v < 0])
        avg_pnl = mean(net_values) if net_values else 0.0
        holdings = [t["holding_minutes"] for t in trades if t["holding_minutes"] is not None]
        holding_results = [
            (float(t["net_pnl"]) / t["holding_minutes"])
            for t in trades
            if t["holding_minutes"] not in (None, 0)
        ]
        fees = self.fees()
        return {
            "total_closed_trades": total,
            "winners": winners,
            "losers": losers,
            "win_rate": (winners / total) if total else 0.0,
            "gross_realized_pnl": float(sum(gross_values)),
            "net_realized_pnl": float(sum(net_values)),
            "average_trade_pnl": float(avg_pnl),
            "best_trade": float(max(net_values)) if net_values else 0.0,
            "worst_trade": float(min(net_values)) if net_values else 0.0,
            "total_fees_paid": fees["total_explicit_fees"],
            "average_holding_minutes": float(mean(holdings)) if holdings else 0.0,
            "average_holding_result_per_trade": float(mean(holding_results)) if holding_results else 0.0,
            "realized_pnl_before_fees": float(sum(gross_values)),
            "realized_pnl_after_fees": float(sum(net_values)),
        }

    def decisions(self, limit: int = 200) -> list[dict]:
        with self.session_factory() as s:
            rows = s.scalars(select(DecisionRecord).order_by(desc(DecisionRecord.id)).limit(limit)).all()
            return [
                {
                    "id": row.id,
                    "ts": row.ts.isoformat() if row.ts else None,
                    "strategy_id": row.strategy_id,
                    "symbol": row.symbol,
                    "side": row.side,
                    "qty": row.qty,
                    "accepted": bool(row.accepted),
                    "reason": row.reason,
                    "mode": row.mode,
                }
                for row in rows
            ]
