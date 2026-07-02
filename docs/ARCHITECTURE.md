# Tradehelm — Architecture

## 1. System overview

```
                       +--------------------+
   yfinance (EOD)  --> |  data/  (cache)    |
                       +---------+----------+
                                 |  daily bars, universe
                                 v
+-------------+        +--------------------+       +------------------+
| advisor/    |        |  strategy/         |       |  backtest/       |
| (Claude API,|        |  deterministic     +-----> |  same strategy,  |
|  advisory   |        |  signals           |       |  simulated fills |
|  ONLY)      |        +---------+----------+       |  + cost + DK tax |
+------+------+                  | target positions +------------------+
       |                         v
       |               +--------------------+
       |               |  risk/             |  sizing, halts, collars
       |               +---------+----------+
       |                         | approved orders
       v                         v
+------------------------------------------------+
|  engine/  (state machine + APScheduler)        |
|  reconcile -> plan -> place -> monitor -> EOD  |
+---------------------+--------------------------+
                      | Broker protocol
            +---------+---------+
            | broker/ SaxoBroker|  OAuth, idempotent orders, resting stops
            |  env: sim | live  |
            +---------+---------+
                      |
        +-------------+--------------+
        | storage/ SQLite (WAL)      |  orders, fills, positions, equity,
        +-------------+--------------+  decisions log, commands, briefings
                      |
            +---------+---------+           +------------------+
            | api/ FastAPI + WS  | <-------> | frontend/ React  |
            +--------------------+           +------------------+
```

One Python process hosts engine + API (engine as asyncio background tasks in the
FastAPI lifespan). A Windows Task Scheduler entry starts it at logon and
restarts on failure. Splitting into two processes later is possible because all
cross-module communication goes through the storage layer and typed interfaces.

## 2. Repository layout

```
tradehelm/
  config/       # pydantic-settings; config.yaml (checked in) + .env (secrets)
  core/         # pydantic models: Instrument, Bar, Signal, TargetPosition,
                #   Order, Fill, Position, PortfolioState, EngineState
  data/         # BarSource, YFinanceSource, ParquetCache, Universe, TradingCalendar
  strategy/     # Strategy protocol + candidates A/B/C (see STRATEGY_SPEC.md)
  risk/         # RiskManager
  backtest/     # engine, CostModel, DanishTaxModel, metrics, walk-forward
  broker/       # Broker protocol, SaxoClient (HTTP/auth), SaxoBroker, BacktestBroker
  engine/       # LiveEngine state machine, scheduler, reconciliation
  api/          # FastAPI app, routes, WebSocket, auth
  advisor/      # Claude API briefings (isolated; see dependency rule)
  storage/      # SQLite repositories, schema migrations (plain SQL scripts)
frontend/       # Vite + React + TS
research/       # trials.csv, REPORT.md, notebooks-as-scripts
scripts/        # pull_data.py, run_backtest.py, run_walkforward.py, auth_setup.py
tests/
docs/
```

Dependency rule (enforced by a test using import inspection): `strategy`,
`risk`, `backtest`, `engine`, `broker` must not import `advisor`, and `advisor`
must not be imported by anything except `api` and the scheduler registration.

## 3. Key interfaces (contracts — keep signatures stable)

```python
class BarSource(Protocol):
    def daily_bars(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...
    # columns: open, high, low, close, adj_close, volume; index: UTC dates

class Strategy(Protocol):
    name: str
    def target_positions(self, ctx: StrategyContext) -> list[TargetPosition]: ...
    # ctx exposes: bars up to and including decision date (never beyond),
    # universe members as of decision date, current portfolio.
    # Returns desired end-state positions incl. stop level per position.

class Broker(Protocol):
    def positions(self) -> list[Position]: ...
    def balances(self) -> Balances: ...
    def place_order(self, order: OrderRequest, request_id: str) -> OrderResult: ...
    def replace_order(...), cancel_order(...), open_orders(...) -> ...
    # SaxoBroker(env="sim"|"live") and BacktestBroker implement this.

class RiskManager:
    def size(self, targets, portfolio, equity_dkk) -> list[OrderPlan]: ...
    def pre_trade_checks(self, plan, portfolio) -> Approval | Rejection: ...
    def monitor(self, portfolio, todays_pnl) -> None | HaltCommand: ...
```

`TargetPosition` carries `symbol, direction (long only in v1), weight_or_risk,
stop_price, time_stop_days, reason` — `reason` is a human-readable audit string
persisted with every decision.

## 4. Backtest engine rules

- Daily event loop over the trading calendar. At each date T: strategy sees data
  through T close; orders generated at T are filled at T+1 open.
- Fills: `open * (1 +/- half_spread + slippage)` per COSTS_AND_TAX.md.
- Protective stops: checked against T+1..Tn bars; if a bar's open gaps through
  the stop, fill at open (not at the stop price). Intraday touch fills at stop.
- Anti-lookahead assertion: StrategyContext raises if any accessor requests a
  date beyond the decision date (tested with a deliberately biased fixture).
- Corporate actions: use adjusted close for signals, raw close + explicit
  dividend cashflows for P&L and tax (dividends are aktieindkomst too).
- Every simulated trade writes the same `decisions` record shape the live
  engine writes, so research output and live logs are directly comparable.

## 5. Risk layer (identical in backtest and live)

| Control | Default (config) |
|---|---|
| Max concurrent positions | 3 (10k DKK) -> scales with equity |
| Per-position risk (entry to stop) | 1.0% of equity |
| Max single-position notional | 40% of equity |
| Max daily loss | 2.0% of equity -> flatten + HALT (human resume) |
| Max drawdown from equity peak | 10% -> HALT (human resume) |
| Order price collar | reject market order if last close deviates > 5% from decision-time close |
| Stale data | no orders if data source's latest bar is older than expected |
| Session dead | no orders; alert; reconcile required before resume |

## 6. Live engine — daily timeline (Europe/Copenhagen; US market)

| Time (CET) | Step |
|---|---|
| 07:30 | Pull EOD bars for prior US session; compute signals; write today's PLAN (orders + stops) to DB; advisor pre-market briefing |
| 15:00 | Wake check: reconcile broker vs DB; verify session alive; verify plan still valid (price collars) |
| 15:30 (US open) | Place entry orders (market-on-open semantics) + protective GTC stop orders for new positions; replace/trail stops for held positions per strategy |
| 16:00 | Confirm fills; write actual vs plan; alert on partials/rejects |
| 22:05 (US close) | EOD reconcile; equity snapshot; realized P&L + running tax accrual update; advisor post-close report |

Missed-run recovery: on any engine start, determine current phase from the clock
and calendar; run reconcile; **never place catch-up entries mid-session** —
missed entries are skipped (logged), exits/stop maintenance are always allowed.
Because stops rest at Saxo as GTC orders (decision D4), a sleeping PC never
leaves a position unprotected.

DST note: CET/EST offsets shift twice a year; all scheduling is defined in
exchange time via the trading calendar, rendered to local time, never hardcoded.

## 7. Saxo integration specifics

- OAuth authorization-code flow; access tokens ~20 min, refreshed via refresh
  token; initial login is interactive (browser) via `scripts/auth_setup.py`;
  tokens stored via `keyring` (Windows DPAPI). Dead session (refresh failed) ->
  EngineState AUTH_REQUIRED + UI banner/notification.
- Rate limits: 120 req/min per service group, 1 order/s, unique `x-request-id`
  per logical order (Saxo returns 409 for identical ops within 15 s otherwise).
  The broker layer serializes order submission with a 1.1 s spacing.
- Orders v1: market entries at open, GTC stop-loss exits, limit orders optional
  later. All order types via the SIM environment first.
- Instrument mapping: Saxo `Uic` <-> ticker resolved via reference-data endpoint
  and cached in SQLite.
- Market data: v1 needs none in real time (EOD cycle, D4). Saxo chart endpoints
  may serve as a secondary EOD source/cross-check.

## 8. Storage (SQLite, WAL mode)

Tables: `orders`, `fills`, `positions_snapshots`, `equity_curve`, `decisions`
(every strategy/risk decision incl. reason + inputs hash), `commands` (UI ->
engine, with actor + timestamp), `briefings`, `tax_lots` (per-instrument
average-cost basis + per-year realized ledger), `events` (auth, halts, errors).
Single file, nightly copy to a backup folder (runbook). DB path in config.

## 9. UI (Phase 5)

Single-page React app served by FastAPI. Views: Dashboard (equity curve,
positions + distance-to-stop, engine state, today's plan vs fills), History
(trades, per-year realized P&L and tax accrual), Briefings, Controls
(pause / resume / flatten-all / kill, each with confirm + audit log), Config
(read-only view of active config + TODO-VERIFY flags). Auth: single bearer
token; server binds to localhost + Tailscale interface only. WebSocket pushes
state changes; REST for history.

## 10. Design decision log

| Decision | Alternative rejected | Why |
|---|---|---|
| Single process (engine in FastAPI lifespan) | Separate daemon + IPC | Less code, fewer failure modes at this scale; storage-mediated interfaces keep the split possible |
| SQLite WAL | Postgres | Single user, single machine; zero ops |
| EOD cycle, no real-time data | Streaming intraday | Kills data costs, fits swing horizon, halves engine complexity; revisit only after live evidence |
| Resting GTC stops at broker | Engine-monitored stops | PC sleep must not expose positions |
| yfinance + bundled constituents CSV | Paid point-in-time data | No spend before Gate 3G; limitations documented in research REPORT |
| React SPA | Streamlit | Owner chose remote/phone-friendly dashboard; engine/UI decoupling |
