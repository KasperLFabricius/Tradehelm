"""Data-quality checks over cached bars.

Two checks (docs/PLAN.md Phase 1 acceptance):
1. Missing sessions vs the NYSE calendar (gaps in coverage).
2. Split sanity on three known cases - the ADJUSTED close must be continuous
   across the split (no split-sized discontinuity), regardless of how the raw
   close is stored.

The logic here is pure and unit-tested with synthetic frames; scripts/data_quality.py
is the thin CLI that runs it over the real cache and writes docs/DATA_QUALITY.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .calendar import TradingCalendar

# Adjusted close should not jump by more than this across a split (it should be
# continuous); a split-sized ratio (>= ~2) trips the WARN.
_ADJ_CONTINUITY_TOL = 0.20


@dataclass(frozen=True)
class SplitCase:
    symbol: str
    date: str  # split effective (ex-) date, ISO
    ratio: float  # e.g. 4.0 for a 4:1 split
    name: str


# Well-known forward splits used as sanity anchors.
KNOWN_SPLITS: tuple[SplitCase, ...] = (
    SplitCase("AAPL", "2020-08-31", 4.0, "Apple 4:1"),
    SplitCase("TSLA", "2020-08-31", 5.0, "Tesla 5:1"),
    SplitCase("NVDA", "2021-07-20", 4.0, "NVIDIA 4:1"),
)


def missing_sessions_for(
    df: pd.DataFrame,
    calendar: TradingCalendar,
    start=None,
    end=None,
) -> pd.DatetimeIndex:
    """Trading sessions absent from df.index over [start, end] (defaults to span)."""
    start = df.index.min() if start is None else start
    end = df.index.max() if end is None else end
    return calendar.missing_sessions(df.index, start, end)


def split_check(
    df: pd.DataFrame, split_date, ratio: float, tol: float = _ADJ_CONTINUITY_TOL
) -> dict:
    """Sanity-check a known split: adjusted close should be continuous across it."""
    day = pd.Timestamp(split_date).normalize()
    before = df.index[df.index < day]
    on_after = df.index[df.index >= day]
    if len(before) == 0 or len(on_after) == 0:
        return {"status": "NO_DATA", "raw_ratio": None, "adj_ratio": None, "expected": ratio}

    b, a = before[-1], on_after[0]
    raw_ratio = float(df.loc[b, "close"] / df.loc[a, "close"])
    adj_ratio = float(df.loc[b, "adj_close"] / df.loc[a, "adj_close"])
    adjusted_continuous = abs(adj_ratio - 1.0) <= tol
    return {
        "status": "PASS" if adjusted_continuous else "WARN",
        "raw_ratio": raw_ratio,
        "adj_ratio": adj_ratio,
        "expected": ratio,
    }


def build_report(
    coverage: list[dict], splits: list[dict], missing_symbols: list[str] | None = None
) -> str:
    """Render a Markdown data-quality report from precomputed rows."""
    missing_symbols = missing_symbols or []
    lines = ["# Data-quality report", "", "## Coverage (missing sessions)", ""]
    if coverage:
        lines += ["| symbol | bars | missing | first | last |", "|---|---|---|---|---|"]
        for row in coverage:
            lines.append(
                f"| {row['symbol']} | {row['bars']} | {row['missing']} | "
                f"{row['first']} | {row['last']} |"
            )
    else:
        lines.append("_no symbols in cache_")

    # Universe members with no cached data at all - exactly the delisted / renamed
    # / no-data constituents the report is meant to surface (never silently drop).
    lines += ["", f"## Universe members with no cached data ({len(missing_symbols)})", ""]
    lines.append(", ".join(missing_symbols) if missing_symbols else "_none_")

    lines += ["", "## Split sanity (adjusted close continuity)", ""]
    lines += ["| case | status | raw ratio | adj ratio | expected |", "|---|---|---|---|---|"]
    for row in splits:
        raw = "-" if row["raw_ratio"] is None else f"{row['raw_ratio']:.2f}"
        adj = "-" if row["adj_ratio"] is None else f"{row['adj_ratio']:.3f}"
        lines.append(
            f"| {row['name']} | {row['status']} | {raw} | {adj} | {row['expected']:.0f}:1 |"
        )
    lines.append("")
    return "\n".join(lines)
