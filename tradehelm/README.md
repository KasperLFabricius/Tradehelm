# TradeHelm v1

TradeHelm v1 is a local-first, broker-agnostic, human-supervised intraday trading simulator.

## What v1 does today
- Runs locally with FastAPI + Streamlit + SQLite.
- Supports bot modes: `STOPPED`, `OBSERVE`, `PAPER`, `HALTED`, `KILL_SWITCH`.
- Replays local intraday CSV bars in a background worker thread (non-blocking control API).
- Executes simulated market/limit orders through a paper broker with partial fills.
- Applies configurable friction with explicit commission fees and implicit spread/slippage price impact (plus tick-size rounding).
- Persists operational records (orders, fills, positions, closed trades, state transitions, logs, replay sessions).
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

The launcher ensures DB/schema initialization, then starts FastAPI on `http://127.0.0.1:8000`.

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
