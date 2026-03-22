# TradeHelm v1

TradeHelm v1 is a local-first, broker-agnostic, human-supervised intraday trading simulator.

## What v1 does today
- Runs locally with FastAPI + Streamlit + SQLite.
- Supports bot modes: `STOPPED`, `OBSERVE`, `PAPER`, `HALTED`, `KILL_SWITCH`.
- Replays local intraday CSV bars in a background worker thread (non-blocking control API).
- Executes simulated market/limit orders through a paper broker with partial fills.
- Applies configurable friction with explicit commission fees and implicit spread/slippage price impact (plus tick-size rounding).
- Persists operational records (orders, fills, positions, closed trades, state transitions, logs, replay sessions).
- Includes extension interfaces/stubs for future broker/news/AI modules.

## What v1 does NOT do
- No Saxo or any live broker integration.
- No paid feeds or premium services.
- No live market data connectors.
- No production-grade execution microstructure model.

## Repository layout
```
tradehelm/
  sample_data/
  src/tradehelm/
    control_api/
    dashboard/
    trading_engine/
    infrastructure/
    providers/
    strategies/
    risk/
    persistence/
    config/
  tests/
```

## Setup (Windows PowerShell)
```powershell
cd tradehelm
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
```

## Run backend
```powershell
uvicorn tradehelm.control_api.app:app --reload --port 8000
```

## Run dashboard
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

## Config updates
`POST /config` applies updates to active components immediately for:
- replay speed
- risk checks for subsequent validations
- cost model for subsequent fills and risk edge estimates

Existing persisted orders/fills/positions are not wiped by config changes.


### Friction accounting model
- Commission is charged as explicit `fee` on fills.
- Spread/slippage are modeled as implicit execution-price impact via adjusted fill prices.
- Trade-evaluation round-trip estimates include both explicit commission and implicit impact.

## Known simulator limitations
- Partial-fill model is intentionally simple.
- Limit-order fill assumptions are simplified.
- Replay pacing uses `sleep` derived from `replay_speed` for deterministic local control, not exchange-accurate timing.

## Future extension points already present
- `BrokerProvider` (for future SaxoBrokerProvider)
- `NewsProvider` (stub)
- `AIScorer` (stub)
