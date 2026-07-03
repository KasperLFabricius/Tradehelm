# Tradehelm — Roadmap and Phase Plan

Owner: Kasper Lindskov Fabricius. Planner/reviewer: Fable. Implementer: Opus.

## 1. Decisions record (settled — do not relitigate during implementation)

| # | Decision | Rationale |
|---|---|---|
| D1 | **Swing trading (hold 2–15 days), not intraday** | Cost drag scales with round-trips; Saxo OpenAPI (REST, 1 order/s, no colocation) suits minute+ decisions; fewer trades reduces naering-reclassification risk |
| D2 | **Cash equities, US large caps first** (OMXC25 optional later) | Matches "select stocks from a universe"; no leverage while unproven; best free-data coverage |
| D3 | **USD sub-account** — convert DKK once, trade within USD | Avoids per-trade FX conversion drag |
| D4 | **EOD decision cycle**: signals from daily bars after close, orders placed pre-open, protective stops rest at the broker | Removes real-time market-data subscription costs entirely for v1; resting GTC stops mean positions stay protected even if the PC sleeps |
| D5 | **Deterministic strategy core; LLM advisory-only** | An LLM's judgment cannot be backtested; the go/no-go gate is a rigorous after-cost after-tax backtest |
| D6 | **FastAPI + React web UI**, engine on owner's Windows PC, remote access via Tailscale | Owner choice; sleep/wake recovery is a first-class requirement |
| D7 | **Free-tier data first** (yfinance daily bars), source-agnostic data layer | No recurring cost before evidence of edge; upgrade path documented |
| D8 | **Phased capital**: backtest -> SIM -> 10k DKK live (proves *plumbing*, not profit) -> scale only when live tracks SIM | A 10k account cannot statistically validate profitability; profitability is decided in Phases 3 and 7 |
| D9 | Start model class: **hardcoded rules / classical ML**, no neural nets, no deep RL in v1 | Low signal-to-noise + non-stationarity makes deep models overfit; edge lives in costs, features and risk control |

## 2. Phase status

| Phase | Title | PR | Status |
|---|---|---|---|
| 0 | Scaffolding + CI | [#9](https://github.com/KasperLFabricius/Tradehelm/pull/9) | Merged |
| 1 | Data layer | [#10](https://github.com/KasperLFabricius/Tradehelm/pull/10) | Merged |
| 2a | Cost + Danish tax models | [#11](https://github.com/KasperLFabricius/Tradehelm/pull/11) | Merged |
| 2b | Backtest engine + metrics + walk-forward | [#12](https://github.com/KasperLFabricius/Tradehelm/pull/12) | Merged |
| 3a | Strategy candidates A/B/C + indicators + benchmark | [#13](https://github.com/KasperLFabricius/Tradehelm/pull/13) | Merged |
| 3b | Research study harness (walk-forward + trials + REPORT.md) | [#14](https://github.com/KasperLFabricius/Tradehelm/pull/14) | Merged |
| 3c | Pre-study fixes from Fable review (docs/REVIEW_PHASE_0-3.md F1-F9) | [#17](https://github.com/KasperLFabricius/Tradehelm/pull/17) | in review |
| 3G | **GATE: go/no-go on strategy results** | — | **BLOCKED: awaiting study run on real data** |
| 4 | Saxo SIM broker + live engine + risk layer | — | not started (gated by 3G) |
| 5 | API + web UI | — | not started |
| 6 | Advisor briefings | — | not started |
| 7 | SIM paper run (>= 4 weeks) + ops runbook | — | not started |
| 7G | **GATE: live checklist sign-off** | — | — |
| 8 | Live at 10k DKK (plumbing validation) | — | not started |

Update this table as PRs open/merge.

**Current checkpoint (all Phase 0-3 code merged).** Every buildable component up to
Gate 3G exists and is tested: data layer, cost/tax models, the event-driven backtest
engine, the three strategy candidates, and the research harness. What remains before
Gate 3G is *running* the study on the real 2005->now universe to produce
`research/REPORT.md` (`python -m scripts.pull_data` then `python -m scripts.run_research`).
That pull cannot run on the current dev box (corporate TLS blocks yfinance), so it is an
owner/data-machine step. Per Gate 3G this is a hard stop: Phases 4-8 build only
infrastructure and must not start until a candidate passes the go/no-go, and the
one-shot holdout is not run until owner + reviewer approve.

## 3. Phases in detail

### Phase 0 — Scaffolding
Scope: repo layout per ARCHITECTURE.md section 2; requirements.txt; ruff + pytest
config; GitHub Actions CI (lint + tests on PR); `.env.example`; `tradehelm/config`
with pydantic-settings loading `config.yaml` + `.env`.
**Acceptance:** CI green on a trivial test; `config.load()` round-trips a sample
config; no phase-specific code.

### Phase 1 — Data layer
Scope: `tradehelm/data`.
- `BarSource` protocol: `daily_bars(symbol, start, end) -> DataFrame[OHLCV, adj_close]`.
- `YFinanceSource` implementation; retry/backoff; explicit failure on gaps.
- `ParquetCache` wrapper (cache dir configurable; incremental updates).
- `Universe`: point-in-time S&P 500 membership from a bundled historical
  constituents CSV (public dataset; document provenance and its limitations).
  `universe.members(date) -> list[symbol]`.
- `TradingCalendar` (NYSE) via `exchange_calendars`.
**Acceptance:** unit tests with recorded fixtures (no network in CI); a
`scripts/pull_data.py` that populates the cache for the full universe 2005->now;
documented data-quality report (missing bars, splits sanity check on 3 known cases).

### Phase 2 — Backtester + cost/tax models
Scope: `tradehelm/backtest`, per ARCHITECTURE.md section 4 and COSTS_AND_TAX.md.
- Event-driven daily loop: bar close -> signals -> next-open fills. No intrabar
  peeking; stops modeled with open-gap logic (gap through stop fills at open).
- `CostModel` and `DanishTaxModel` exactly per COSTS_AND_TAX.md (test-first from
  its worked examples).
- Metrics: net CAGR, max drawdown, Sharpe, deflated Sharpe (needs trials.csv),
  profit factor, exposure, turnover, per-year after-tax P&L table.
- Walk-forward runner: rolling 3y train / 1y test, 5-day purge, plus a final
  2-year untouched holdout runnable only via an explicit `--holdout` flag.
**Acceptance:** golden test — buy-and-hold SPY through the engine matches a
hand-computed result within tolerance, including tax; tax model property tests
pass (loss ring-fencing, average-cost basis, threshold progression); a
deliberately look-ahead-biased strategy fixture is caught by the engine's
anti-lookahead assertion.

### Phase 3 — Strategies + research study
Split into two PRs (as Phase 2 was): **3a** delivers the deterministic alpha logic
— the indicator library, candidates A/B/C, and the SPY buy-and-hold benchmark, plus
a `weight=None` HOLD semantic on the engine so multi-day/weekly positions are not
resized by daily drift — all unit-tested on synthetic panels (no network). **3b**
delivers the research harness (walk-forward selection, `research/trials.csv`,
deflated Sharpe over the true trial count, doubled-cost + 27/42 stress lines) and
`research/REPORT.md`; running it needs the full 2005->now data pull, which the
corporate-TLS dev box cannot fetch locally, so the study is executed where data is
available and is what Gate 3G reads.

Scope: implement candidates A, B, C exactly per STRATEGY_SPEC.md; run the full
validation protocol; write `research/REPORT.md` with results net of everything,
parameter-plateau plots, and per-window OOS table. Every run logged to
`research/trials.csv`.
**Acceptance:** REPORT.md complete; deflated Sharpe computed over the true trial
count; benchmark comparison (after-tax SPY buy-and-hold) included; no holdout
run until owner + reviewer approve using it.

### GATE 3G — go/no-go (owner + Fable)
Pass requires, on OOS walk-forward net of costs and tax: risk-adjusted return
better than after-tax SPY buy-and-hold; positive net result in >= 60% of test
windows; performance is a parameter *plateau*, not a spike; then a single
holdout run confirming. **If no candidate passes, the project stops here or
returns to research — Phases 4-8 build no alpha, only infrastructure.**

### Phase 4 — Saxo SIM broker + live engine + risk layer
Scope: `tradehelm/broker`, `tradehelm/engine`, `tradehelm/risk`.
- OAuth code flow against SIM; token store (Windows DPAPI via `keyring`);
  automatic refresh; clean "session dead -> alert, await re-auth" state.
- Broker protocol impl: place/replace/cancel orders with unique `x-request-id`
  (idempotency), positions, balances, order events via streaming or polling.
- Reconciliation: on every engine start and before any order burst, broker state
  is authoritative; mismatch -> HALTED.
- Risk layer per ARCHITECTURE.md section 5 (sizing, max positions, daily-loss
  halt, drawdown halt, order price collars, stale-data refusal).
- Engine state machine + APScheduler daily timeline per ARCHITECTURE.md
  section 6, including missed-run/sleep-wake recovery.
**Acceptance:** scripted end-to-end SIM demo: auth -> reconcile -> place entry +
protective stop -> restart engine mid-session -> reconcile detects position ->
EOD report written. Risk-halt unit tests. Chaos tests: token expiry mid-cycle,
duplicate submission attempt, order rejection handling.

### Phase 5 — API + web UI
Scope: `tradehelm/api`, `frontend/`.
- FastAPI: `/status`, `/positions`, `/orders`, `/equity`, `/trades`,
  `/briefings`, `POST /commands` (pause | resume | flatten-all | kill),
  WebSocket `/stream` for pushes. Single-user bearer token auth; binds to
  localhost/Tailscale interface only.
- React dashboard: equity curve, open positions with stops, today's plan,
  order/trade log, engine state + halts, command buttons with confirm dialogs,
  config viewer. Mobile-usable layout.
**Acceptance:** UI drives a full SIM session; commands round-trip and are logged
with attribution; kill switch works while orders are pending.

### Phase 6 — Advisor (LLM briefings)
Scope: `tradehelm/advisor`. Pre-market briefing (positions, today's planned
orders, earnings-calendar flags if a free source is wired, notable overnight
moves) and post-close narration, via Claude API (model configurable, default
`claude-opus-4-8`). Stored in SQLite, rendered in the UI. Nothing in the trade
path reads advisor output (enforced by module dependency test).
**Acceptance:** briefings generate on schedule, cost per day logged, graceful
skip on API failure (never blocks trading).

### Phase 7 — SIM paper run + ops runbook
Scope: run the full system on SIM for >= 4 consecutive weeks at *medium*
simulated size (not 10k). `docs/RUNBOOK.md`: start/stop, re-auth procedure,
halt recovery, Tailscale setup, Task Scheduler watchdog config, backup of the
SQLite DB.
**Acceptance:** 4-week SIM log shows: no unexplained divergence from expected
strategy behavior; every sleep/wake and token event handled per design; live-SIM
results statistically consistent with the backtest's expectation band.

### GATE 7G — live checklist (owner signs each line)
Broker mismatch drill passed; kill switch drill passed; PC-asleep-during-open
drill passed (stops rested at broker); tax/fee config values verified against
current Saxo price list and revisor advice (all TODO-VERIFYs resolved); owner
accepts max-drawdown number in writing.

### Phase 8 — Live, 10k DKK
Purpose: **validate plumbing with real money, not profitability.** Success =
zero correctness incidents over the run; live behavior consistent with SIM.
Scaling to medium size is a new decision for the owner, gated on Phase 7's
statistical evidence, not on the 10k P&L.

## 4. Owner action items (not for the implementing agent)

1. Create a Saxo developer-portal account; register an OpenAPI app for **SIM**
   (redirect `http://localhost:<port>/callback`); note AppKey/AppSecret.
2. Confirm with a revisor: 2026 aktieindkomst progression threshold; naering
   risk at this trade frequency; treatment of the USD sub-account conversions.
3. Verify current Saxo DK price list values in `config.yaml` (commission rates,
   minimums, FX conversion rate, custody fee) — all marked TODO-VERIFY.
4. Install Tailscale on PC + phone (Phase 5).
5. Configure Windows power settings: no sleep during US market hours on trading
   days, or accept the documented catch-up behavior (Phase 4).

## 5. Risks register (top 5)

| Risk | Mitigation |
|---|---|
| No strategy survives costs+tax (likely!) | Gate 3G stops spend before broker/UI phases; candidate C (low turnover) is the fallback |
| Free data quality poisons research | Phase 1 data-quality report; corporate-action spot checks; upgrade path to paid EOD data |
| Overfitting via many research trials | Mandatory trials.csv + deflated Sharpe; single-shot holdout |
| PC sleep during market hours | Resting broker-side stops (D4); reconcile-on-wake; owner action item 5 |
| Saxo session death / token expiry | Dead-session state + UI/notification alert; no orders without fresh reconcile |
