# TradeHelm v1

Local-first, broker-agnostic, human-supervised intraday stock trading simulator.

## Features in this first pass
- Broker-agnostic interfaces (BrokerProvider, MarketDataProvider, Strategy, CostModelProvider, NewsProvider stub, AIScorer stub)
- Modes: STOPPED, OBSERVE, PAPER, HALTED, KILL_SWITCH
- FastAPI control API with required endpoints
- Streamlit live dashboard with command center, strategies, orders/fills, positions, risk, logs
- Replay market data provider from local CSV
- Paper broker with market/limit orders, cancellations, partial fills, persistent state, PnL, trade journal
- Friction model: commission, minimum commission, spread, slippage, tick-size rounding
- Layered risk checks including net-edge-after-friction rejection
- SQLite persistence for state transitions, config, orders, fills, positions, closed trades, logs, replay metadata

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

## Demo replay flow
1. Start backend.
2. Open dashboard.
3. In Replay section, load `sample_data/demo_intraday.csv`.
4. Set mode to `PAPER` or `OBSERVE`.
5. Click **Start replay**.
6. Watch orders/fills/positions/logs update.
7. Trigger **Kill Switch** to flatten and cancel working orders.

## Architecture summary
- `trading_engine`: core deterministic logic (state machine, risk engine, paper broker, cost model, replay orchestration)
- `providers`: stable extension interfaces and replay data provider
- `strategies`: demo strategies (`NoOpStrategy`, `OpeningRangeBreakoutStrategy`)
- `control_api`: operator command and monitoring endpoints
- `dashboard`: local operator UI (Streamlit)
- `persistence`: SQLite schema and session setup
- `config`: strongly typed Pydantic config models

## Notes
- No Saxo integration in v1.
- No paid services required.
- No live third-party data dependency.
- Design preserves future plugin points for SaxoBrokerProvider, premium news, AI scoring, and calendar modules.
