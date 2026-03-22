# TradeHelm v1

TradeHelm v1 is a local-first, broker-agnostic, human-supervised intraday trading simulator.

## What v1 does today
- Runs locally with FastAPI + Streamlit + SQLite.
- Supports bot modes: `STOPPED`, `OBSERVE`, `PAPER`, `HALTED`, `KILL_SWITCH`.
- Replays local intraday CSV bars in a background worker thread (non-blocking control API).
- Executes simulated market/limit orders through a paper broker with partial fills.
- Applies configurable friction with explicit commission fees and implicit spread/slippage price impact (plus tick-size rounding).
- Persists operational records (orders, fills, positions, enriched closed trades, state transitions, logs, replay sessions, decision audit records).
- Persists active runtime config and safe runtime metadata (replay path/mode/speed) across restarts.
- Includes extension interfaces/stubs for future broker/news/AI modules.

## Setup (Windows PowerShell)
```powershell
cd tradehelm
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

## Single-command backend startup
```powershell
python -m tradehelm
```

The launcher ensures DB/schema initialization, then starts FastAPI on `http://127.0.0.1:8000`. Existing local SQLite files are upgraded in-place on startup for backward-compatible schema additions.

## Dashboard startup (second terminal)
```powershell
streamlit run src/tradehelm/dashboard/app.py
```

## Replay behavior
1. Load dataset (default: `sample_data/demo_intraday.csv`).
2. Set mode to `OBSERVE` or `PAPER`.
3. Start replay (`POST /replay/start` or dashboard button).
4. Replay runs in a background thread; API stays responsive.
5. Stop replay with `POST /replay/stop`.

### Mode/control semantics during replay
- `HALTED`: replay keeps advancing market data and mark-to-market, but no new entries are opened.
- `STOPPED`: requests replay stop and exits worker loop.
- `KILL_SWITCH`: requests replay stop, cancels working simulated orders, and flattens simulated positions.

## Historical backtesting (v1 scope)

TradeHelm now supports local-first historical ingestion/caching and cached-data backtests with **Twelve Data** as the first provider.

### Scope in this PR
- Provider: Twelve Data only
- Market: US equities only (symbol validation is intentionally conservative)
- Supported intervals: `1min`, `5min`, `15min`, `30min`, `1h`
- Backtests run from local cached snapshots (no live fetch during run)

### Twelve Data API key
Set an environment variable before starting API/dashboard:

```powershell
$env:TWELVE_DATA_API_KEY=\"your_key_here\"
```

### Historical API workflow
- `POST /historical/fetch` (or `POST /historical/prepare`)
  - Request shape:
    - `symbols: list[str]`
    - `start_date: YYYY-MM-DD`
    - `end_date: YYYY-MM-DD`
    - `interval: \"1min\" | \"5min\" | \"15min\" | \"30min\" | \"1h\"`
    - `adjusted: bool`
- `GET /historical/datasets` lists cached datasets
- `GET /historical/cache` lists cache files
- `POST /backtests/jobs` queues a cached-data backtest job (async)
- `GET /backtests/jobs` lists jobs with live status/progress
- `GET /backtests/jobs/{job_id}` returns current job snapshot
- `POST /backtests/jobs/{job_id}/cancel` requests cooperative cancellation
- `GET /backtests/jobs/{job_id}/events` returns the job activity feed
- `POST /backtests/run` remains as compatibility alias to job enqueue
- `GET /backtests/runs` lists persisted backtest runs
- `POST /backtests/compare` compares two or more run IDs with side-by-side metrics
- `GET /backtests/{run_id}` returns run-scoped review artifacts (config snapshot, summary, trades, decisions, equity curve, symbol summary, per-strategy summary, trade timeline, decision summary, event timeline)

Backtest execution is isolated and reproducible: each run executes in its own temporary SQLite context, so existing replay/paper records in the main app DB cannot contaminate that run’s summary. Each run persists a full config snapshot (`config_json`) including active friction/risk/strategy settings, enabled strategies, interval, symbols, and exact dataset keys used so post-run analysis remains immutable even after later config changes.

### Strategy Lab workflow
- Build a **New Experiment** request with symbols/date/interval/adjusted, enabled strategies, and per-strategy parameter overrides.
- Optional per-run friction/risk overrides are applied to that experiment only; global app config is not mutated.
- Queue a job and monitor `QUEUED → RUNNING → COMPLETED/FAILED/CANCELLED` with percent progress and current symbol/timestamp.
- Review results in richer run detail panels and compare runs including drawdown, expectancy, trades/day, per-symbol and per-strategy diagnostics.

### Strategy catalog and new candidates
- `GET /backtests/strategies/catalog` exposes strategy metadata for UI form generation (`strategy_id`, display name, description, regime type, interval hints, defaults).
- Existing strategies remain available: ORB and VWAP continuation.
- New candidates:
  - **Gap-filtered ORB** (`gap_orb`): ORB with minimum opening gap/opening-range/volume preconditions.
  - **VWAP mean reversion** (`vwap_mean_reversion`): enters on controlled reversion after VWAP stretch.

### Adjustment behavior
- Intraday bars are fetched unadjusted and then adjusted client-side when `adjusted=true`.
- Split adjustment is enabled in v1 and applied to pre-ex-date bars.
- Dividend adjustment is implemented as optional/simple logic in code but disabled by default for ingestion (`adjusted=true` currently means split-adjusted intraday bars).
- Results should be treated as deterministic diagnostics, not accounting-grade corporate-action normalization.

### Local cache behavior
- Cached under `historical_cache/` by default.
- Deterministic cache key includes provider, symbol, interval, date range, and adjusted flag (interval is part of dataset identity).
- Each dataset stores:
  - `bars.csv`
  - `splits.csv`
  - `dividends.csv`
- Metadata index is persisted in SQLite (`historical_datasets` table).

### Analyzer/observer-style backtest artifacts
Each completed run now stores deterministic review artifacts derived only from the isolated run DB:
- equity curve points (`timestamp`, `equity`, `realized_pnl`, `unrealized_pnl`)
- per-symbol summary (`trades`, `net_pnl`, `win_rate`, `total_fees`)
- decision summary (`accepted/rejected`, counts by reason, counts by strategy)

### Current limitations
- No non-US symbols
- No multi-provider routing
- No cloud storage or optimizer/portfolio optimizer
- No live broker integration
- No parameter sweeps/grid search
- No ML/AI/news/calendar factors


## Replay review analytics (paper-trading)
TradeHelm now includes a deterministic replay-review layer intended for strategy diagnostics (not strategy optimization yet).

### API endpoints
- `GET /analytics/summary`: core session/trade metrics.
- `GET /analytics/trades`: enriched closed trade journal (`entry_ts`, `exit_ts`, `side`, `gross_pnl`, `fees`, `net_pnl`, holding minutes).
- `GET /analytics/sessions`: replay session metadata (`dataset`, `loaded_at`, `started_at`, `completed_at`, `status`).
- `GET /analytics/fees`: total explicit fees from persisted fills.
- `GET /analytics/decisions`: structured accept/reject trail for strategy intents.
- `POST /analytics/reset` with `{"confirm": true}` clears simulated analytics records for a fresh run.

### Summary metrics
`/analytics/summary` includes:
- total closed trades
- winners / losers / win rate
- gross realized PnL (before explicit fees)
- net realized PnL (after explicit fees)
- average, best, and worst trade PnL
- total fees paid
- average holding minutes
- average holding result per trade (net PnL per holding minute, where duration > 0)

### Important limitations
- The simulator remains simplified and local-only; no live broker/data integration is introduced.
- PnL attribution is derived from persisted simulated fills/trades and should be treated as diagnostic-grade, not production-grade accounting.
- Slippage/impact are represented via configured execution-price adjustment assumptions, not full market microstructure.


## Deterministic intraday strategies (paper mode)

TradeHelm now ships with two deterministic strategy families designed for replay diagnostics and controllable behavior:

- **ORB (Opening Range Breakout)**
  - Configurable opening range window (bars).
  - Single breakout entry per symbol/session by default (anti-spam).
  - Direction mode: `LONG`, `SHORT`, or `BOTH`.
  - Configurable breakout buffer, fixed stop-loss, fixed take-profit, max-bars-in-trade, and optional end-of-session flatten.
  - Emits explicit decision reasons like `orb_breakout_long`, `orb_stop_exit`, `orb_target_exit`, `orb_max_bars_exit`.

- **VWAP continuation / pullback**
  - Uses deterministic intraday cumulative VWAP alignment.
  - Waits for pullback toward VWAP and re-expansion before entry.
  - Direction mode: `LONG`, `SHORT`, or `BOTH`.
  - Configurable pullback threshold, re-entry buffer, fixed stop-loss, fixed take-profit, and max-bars-in-trade exits.
  - Emits explicit reasons like `vwap_pullback_entry`, `vwap_stop_exit`, `vwap_target_exit`, `vwap_max_bars_exit`.

### Strategy configuration visibility
- Strategy parameters are typed and persisted under the top-level `strategies` section in `/config`.
- `/strategies` reports enabled status plus per-strategy config/state snapshots for dashboard diagnostics.

## API highlights
- `/health` includes readiness snapshot (DB, replay, mode, config loaded).
- `/config` returns/updates persisted runtime config.
- Structured error JSON is returned for common operator errors (`invalid_replay_path`, `strategy_not_found`, `replay_not_loaded`, etc.).

## CI
GitHub Actions workflow at `.github/workflows/ci.yml` runs:
- dependency install
- API import smoke check
- `pytest -q`

## Out of scope for v1
- No Saxo or any live broker integration.
- No paid feeds or premium services.
- No live market data connectors.
- No production-grade execution microstructure model.
