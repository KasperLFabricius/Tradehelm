# scripts/

Standalone entry points (run from the repo root). Added in their phases:

- `pull_data.py` - populate the local data cache (Phase 1)
- `run_backtest.py` - run a single backtest configuration (Phase 2)
- `run_walkforward.py` - walk-forward validation runner (Phase 2/3)
- `auth_setup.py` - interactive Saxo OAuth login, stores tokens (Phase 4)

None exist yet; this file marks the directory and its intended contents.
