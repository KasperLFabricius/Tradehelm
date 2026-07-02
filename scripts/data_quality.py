"""Generate the data-quality report over the cached bars.

Requires a populated cache (run scripts/pull_data.py first). Writes
docs/DATA_QUALITY.md. Offline once the cache exists.

Usage (from the repo root):
    python -m scripts.data_quality [--cache DIR] [--out docs/DATA_QUALITY.md] [--limit N]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from tradehelm.config import load
from tradehelm.data import ParquetCache, TradingCalendar, Universe
from tradehelm.data.quality import (
    KNOWN_SPLITS,
    build_report,
    missing_sessions_for,
    split_check,
)


def _parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Data-quality report over the bar cache.")
    ap.add_argument("--cache", default=None)
    ap.add_argument("--out", default="docs/DATA_QUALITY.md")
    ap.add_argument("--limit", type=int, default=None, help="cap symbols in coverage table")
    return ap.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    cache_dir = args.cache or load().app.data.cache_dir
    cache = ParquetCache(cache_dir)
    calendar = TradingCalendar()

    symbols = Universe.default().all_symbols()
    if args.limit is not None:
        symbols = symbols[: args.limit]

    coverage = []
    for symbol in symbols:
        df = cache.read(symbol)
        if df is None:
            continue
        missing = missing_sessions_for(df, calendar)
        coverage.append(
            {
                "symbol": symbol,
                "bars": len(df),
                "missing": len(missing),
                "first": df.index.min().date().isoformat(),
                "last": df.index.max().date().isoformat(),
            }
        )

    splits = []
    for case in KNOWN_SPLITS:
        df = cache.read(case.symbol)
        result = (
            {"status": "NO_DATA", "raw_ratio": None, "adj_ratio": None, "expected": case.ratio}
            if df is None
            else split_check(df, case.date, case.ratio)
        )
        splits.append({"name": case.name, **result})

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_report(coverage, splits), encoding="utf-8")
    print(f"wrote {out_path} ({len(coverage)} symbols, {len(splits)} split checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
