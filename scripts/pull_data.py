"""Populate the local Parquet cache with daily bars for the S&P 500 universe.

Network- and time-intensive; run manually (never in CI). By default it pulls the
UNION of all historical members so delisted names remain available for
point-in-time backtests (avoids survivorship bias in the universe dimension).

Usage (from the repo root):
    python -m scripts.pull_data --start 2005-01-01
    python -m scripts.pull_data --symbols AAPL,MSFT,NVDA --start 2018-01-01
    python -m scripts.pull_data --limit 25            # first 25 symbols only
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys

from tradehelm.config import load
from tradehelm.data import DataError, ParquetCache, TradingCalendar, Universe, YFinanceSource


def _parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Populate the Tradehelm bar cache.")
    ap.add_argument("--start", default="2005-01-01", help="ISO start date (default 2005-01-01)")
    ap.add_argument(
        "--end", default=dt.date.today().isoformat(), help="ISO end date (default today)"
    )
    ap.add_argument("--cache", default=None, help="Cache dir (default: config data.cache_dir)")
    ap.add_argument(
        "--symbols", default=None, help="Comma-separated symbols (default: full universe)"
    )
    ap.add_argument("--limit", type=int, default=None, help="Only the first N symbols")
    ap.add_argument("--refresh", action="store_true", help="Ignore cache; re-fetch")
    return ap.parse_args(argv)


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    else:
        symbols = Universe.default().all_symbols()
    if args.limit is not None:
        symbols = symbols[: args.limit]
    return symbols


def main(argv=None) -> int:
    args = _parse_args(argv)
    cache_dir = args.cache or load().app.data.cache_dir
    cache = ParquetCache(cache_dir, calendar=TradingCalendar())
    source = YFinanceSource()
    symbols = _resolve_symbols(args)

    ok = failed = 0
    for i, symbol in enumerate(symbols, 1):
        try:
            df = cache.get_or_fetch(symbol, args.start, args.end, source, refresh=args.refresh)
            ok += 1
            print(f"[{i}/{len(symbols)}] {symbol}: {len(df)} bars")
        except DataError as exc:
            failed += 1
            print(f"[{i}/{len(symbols)}] {symbol}: SKIP ({exc})", file=sys.stderr)
    print(f"done: {ok} ok, {failed} failed, cache={cache_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
