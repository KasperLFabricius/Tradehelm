"""Run the Phase 3b research study over cached data and write research/REPORT.md.

Reads the local Parquet cache (populate it first with `python -m scripts.pull_data`),
runs the walk-forward study for each candidate - base, doubled-cost and 27%-flat-tax
stress lines - appends every run to research/trials.csv (never pruned), and renders
the Gate-3G report. Network-free but compute-heavy; run manually, never in CI.

Usage (from the repo root):
    python -m scripts.run_research --start 2005-01-01
    python -m scripts.run_research --candidates c --limit 120   # a fast subset
    python -m scripts.run_research --fx-csv usd_dkk.csv         # real daily FX

The final untouched HOLDOUT is NOT run here (that is a one-shot, gated on owner +
reviewer approval per docs/STRATEGY_SPEC.md); the walk-forward reserves it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import pandas as pd

from tradehelm.backtest import CostModel
from tradehelm.config import load
from tradehelm.data import ParquetCache, TradingCalendar, Universe
from tradehelm.research import build_report, doubled_costs, run_candidate_study
from tradehelm.research.trials import TrialLog
from tradehelm.strategy import RiskParams

_CANDIDATE_NAMES = {"a": "candidate_a", "b": "candidate_b", "c": "candidate_c"}
# A rough USD/DKK used only when no FX series is supplied. A CONSTANT means no FX
# movement is modelled (so no FX taxable gain); supply --fx-csv for the real series.
_DEFAULT_FX_RATE = 6.9  # TODO-VERIFY: use a real daily USD/DKK series before Gate 7G.


def _parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Run the Tradehelm research study.")
    ap.add_argument("--start", default="2005-01-01", help="ISO study start (default 2005-01-01)")
    ap.add_argument("--end", default=dt.date.today().isoformat(), help="ISO end (default today)")
    ap.add_argument("--cache", default=None, help="Cache dir (default: config data.cache_dir)")
    ap.add_argument("--benchmark", default="SPY", help="Benchmark/regime symbol (default SPY)")
    ap.add_argument("--candidates", default="a,b,c", help="Comma list of a/b/c (default all three)")
    ap.add_argument("--initial-dkk", type=float, default=100_000.0, help="Starting capital DKK")
    ap.add_argument("--fx-rate", type=float, default=_DEFAULT_FX_RATE, help="Constant USD/DKK")
    ap.add_argument("--fx-csv", default=None, help="CSV of date,rate for a real USD/DKK series")
    ap.add_argument("--limit", type=int, default=None, help="Only the first N universe symbols")
    ap.add_argument("--no-stress", action="store_true", help="Skip the cost/tax stress lines")
    ap.add_argument("--out-dir", default="research", help="Output dir (default: research/)")
    return ap.parse_args(argv)


def build_panel(cache: ParquetCache, symbols: list[str], benchmark: str) -> dict[str, pd.DataFrame]:
    """Assemble a {symbol: bar frame} panel from the cache, always including the
    benchmark. Symbols with no cached bars are skipped (degraded coverage, not fatal)."""
    panel: dict[str, pd.DataFrame] = {}
    for symbol in {*symbols, benchmark}:
        df = cache.read(symbol)
        if df is not None and len(df):
            panel[symbol] = df
    if benchmark not in panel:
        raise SystemExit(f"benchmark {benchmark!r} is not in the cache - pull it first")
    return panel


def _fx(args: argparse.Namespace):
    if args.fx_csv:
        df = pd.read_csv(args.fx_csv)
        return pd.Series(df.iloc[:, 1].values, index=pd.to_datetime(df.iloc[:, 0]))
    return float(args.fx_rate)


def main(argv=None) -> int:
    args = _parse_args(argv)
    cfg = load().app
    cache = ParquetCache(args.cache or cfg.data.cache_dir, calendar=TradingCalendar())
    calendar = TradingCalendar()
    universe = Universe.default()

    symbols = universe.all_symbols()
    if args.limit is not None:
        symbols = symbols[: args.limit]
    panel = build_panel(cache, symbols, args.benchmark)

    names = [_CANDIDATE_NAMES[c.strip()] for c in args.candidates.split(",") if c.strip()]
    costs = CostModel(cfg.costs)
    costs_x2 = CostModel(doubled_costs(cfg.costs))
    thresholds = cfg.tax.thresholds
    risk = RiskParams.from_config(cfg.risk)
    fx = _fx(args)

    out_dir = Path(args.out_dir)
    trials = TrialLog(out_dir / "trials.csv")

    common = dict(
        panel=panel,
        members_fn=universe.members,
        calendar=calendar,
        tax_thresholds=thresholds,
        start=args.start,
        end=args.end,
        initial_dkk=args.initial_dkk,
        usd_dkk=fx,
        risk=risk,
        trials=trials,
        benchmark_symbol=args.benchmark,
    )

    studies = []
    for name in names:
        print(f"[study] {name} base ...", file=sys.stderr)
        studies.append(
            run_candidate_study(
                name,
                cost_model=costs,
                rate_low=cfg.tax.rate_low,
                rate_high=cfg.tax.rate_high,
                **common,
            )
        )
        if not args.no_stress:
            print(f"[study] {name} costs x2 ...", file=sys.stderr)
            studies.append(
                run_candidate_study(
                    name,
                    cost_model=costs_x2,
                    cost_mult=2.0,
                    rate_low=cfg.tax.rate_low,
                    rate_high=cfg.tax.rate_high,
                    **common,
                )
            )
            print(f"[study] {name} 27% flat tax ...", file=sys.stderr)
            studies.append(
                run_candidate_study(
                    name,
                    cost_model=costs,
                    tax_label="27flat",
                    rate_low=cfg.tax.rate_low,
                    rate_high=cfg.tax.rate_low,
                    **common,
                )
            )

    note = (
        f"Data: local cache, {len(panel)} symbols, {args.start}..{args.end}. "
        f"FX: {'series ' + args.fx_csv if args.fx_csv else f'constant {args.fx_rate} USD/DKK'}."
    )
    report = build_report(studies, trials, data_note=note)
    (out_dir / "REPORT.md").write_text(report, encoding="utf-8")
    print(f"wrote {out_dir / 'REPORT.md'} and {out_dir / 'trials.csv'} ({trials.count()} trials)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
