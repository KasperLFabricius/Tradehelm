# scripts/

Standalone entry points. Run from the repo root **as modules** so `import
tradehelm` resolves, e.g. `python -m scripts.pull_data`.

Phase 1 (present):
- `pull_data.py` - populate the local Parquet cache with daily bars
- `fetch_universe.py` - refresh the bundled S&P 500 membership dataset
- `data_quality.py` - write docs/DATA_QUALITY.md from the cache

Later phases:
- `run_backtest.py` - run a single backtest configuration (Phase 2)
- `run_walkforward.py` - walk-forward validation runner (Phase 2/3)
- `auth_setup.py` - interactive Saxo OAuth login, stores tokens (Phase 4)
