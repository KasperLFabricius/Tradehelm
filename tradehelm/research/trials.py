"""Append-only research trials ledger (research/trials.csv).

Every configuration ever executed - each grid point on each train span, each
out-of-sample test run, each stress rerun - is appended here and NEVER pruned
(CLAUDE.md rule 8). The row count is the true trial count that the deflated Sharpe
must discount against, so silently dropping rows would overstate significance.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass, fields
from pathlib import Path

import pandas as pd


@dataclass(frozen=True)
class Trial:
    """One executed backtest configuration and its net-of-everything result."""

    candidate: str
    phase: str  # "train" | "test" | "holdout"
    window: int
    params: str  # compact "k=v;k=v" of the strategy parameters
    cost_mult: float  # 1.0 base, 2.0 doubled-cost stress line
    tax_label: str  # e.g. "27/42" or "27flat"
    span_start: str  # ISO date
    span_end: str
    n_obs: int  # number of daily returns backing the metrics
    sharpe: float  # annualized, after costs AND tax
    sharpe_pp: float  # per-period Sharpe (feeds the deflated-Sharpe variance)
    cagr: float
    max_drawdown: float
    total_return: float
    final_equity_dkk: float


def params_str(params: dict) -> str:
    """Deterministic compact encoding of a parameter dict for the CSV cell."""
    return ";".join(f"{k}={params[k]}" for k in sorted(params))


class TrialLog:
    """Append rows to a CSV, writing the header once. Reads back for counts/analysis."""

    FIELDS: tuple[str, ...] = tuple(f.name for f in fields(Trial))

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def append(self, trial: Trial) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new_file = not self.path.exists() or self.path.stat().st_size == 0
        with self.path.open("a", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=self.FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(asdict(trial))

    def count(self) -> int:
        """Number of trial rows (excludes the header). 0 if the file is absent."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return 0
        return len(self.read())

    def read(self) -> pd.DataFrame:
        return pd.read_csv(self.path)
