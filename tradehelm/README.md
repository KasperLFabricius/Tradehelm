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
