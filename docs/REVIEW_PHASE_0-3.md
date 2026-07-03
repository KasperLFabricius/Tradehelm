# Fable review - Phases 0-3 (PRs #9-#15)

Reviewer: Fable. Date: 2026-07-03. Scope: everything merged to main through
`90899c4` - scaffolding/config, data layer, cost + Danish tax models, backtest
engine + metrics + walk-forward, strategy candidates A/B/C + benchmark, research
harness + CLI.

Verified before review: 169 tests green, `ruff check` + `ruff format --check`
clean, no non-ASCII in source, no LLM/network imports anywhere in the
deterministic core (CLAUDE.md rules 1 and 9 hold), broker config defaults to SIM.

**Verdict: the architecture and correctness discipline are sound - the engine's
timing model, the tax ledger, and the research protocol are faithful to the
docs and unusually well-tested. But the study is NOT runnable as shipped
(finding F1), and two findings (F2, F3) would bias its results. Do not run the
Gate 3G study until F1-F4 land. None of this requires re-opening merged
design decisions.**

## Findings

### F1 - BLOCKER: the study is computationally infeasible (est. ~1,200 hours)

Strategies recompute every rolling indicator over the full price history on
every decision day: `ctx.history()` returns a growing prefix and
`indicators.rsi/atr/sma/...` re-derive the whole series each call, making each
backtest O(days^2 x symbols). Measured on this machine: one backtest-year of
Candidate A over 100 symbols with ~4y history costs **84 s**; scaled to the
real study (~500 tradable names, 3y-train/1y-test x ~14 windows x grids of
12/8/4 x 3 variants) that extrapolates to **~1,200 hours**.

Fix (safe by construction): every indicator is backward-looking - the value at
row k is invariant to removing future rows (already locked by
`test_indicators_have_no_lookahead`). So precompute each indicator ONCE per
symbol on the full adjusted frame and have strategies read the value at
`ctx.date` instead of recomputing on a prefix. Prescription:

- Add an `IndicatorPanel` (or extend `StrategyContext`) that lazily computes
  and caches, per symbol, the full-series columns the candidates need:
  sma5/sma200, rsi2, atr14, highest/lowest-close for the grid's lookbacks,
  trailing returns r100/r126/r252 (skip 5), median dollar volume 20.
- Candidates look up `.loc[ctx.date]` (or positional index) - no `.loc[:date]`
  slices in the hot path. The anti-lookahead guarantee moves from "recompute on
  a prefix" to "causal columns indexed at the decision date", which the
  existing structural test already proves equivalent.
- Add a regression test: cached-lookup targets == prefix-recompute targets on a
  small panel, for all three candidates.

Expected speedup: 2-3 orders of magnitude (study lands in single-digit hours).

### F2 - HIGH: Candidate C's sizing conflates the catastrophe stop with risk sizing

Spec section "Position sizing" applies `shares = equity*1% / (entry - stop)` to
all candidates. For C, the stop is 20% below entry, so every position sizes to
~5% of equity; with `n_hold = 5` the strategy is ~75% cash at all times. That
structurally dooms the designated low-turnover comparator against an all-in SPY
benchmark and would contaminate the Gate 3G conclusion ("no candidate survives"
when really "C was never invested").

This is a spec bug, not an implementation bug - the implementation is faithful.
As spec author I am amending STRATEGY_SPEC.md: **Candidate C sizes each holding
at `min(1/n_hold, max_position_notional_frac)` of equity; the 20% stop remains a
catastrophe exit only and does not enter the sizing formula.** A and B keep
risk-based sizing (their ATR stops are genuine risk anchors).

### F3 - MEDIUM: `min_ticket_dkk` is specified but never enforced

The spec's sizing section: "skip if resulting notional < min_ticket (config,
default 2,000 DKK equivalent - below this, minimum commission drag exceeds 0.5%
per side)." `RiskConfig.min_ticket_dkk` exists and is validated, but nothing
reads it - the engine will happily buy 1 share. With `min_commission_us` at a
few USD, tiny fills bleed exactly the drag the rule exists to prevent, and the
10k-DKK live phase (Phase 8) trades in this regime. Prescription: enforce at
the engine's buy site (it knows fill price, FX and shares): if
`shares x fill x fx < min_ticket_dkk`, skip the buy. Thread the value through
`BacktestEngine.__init__` (default 0 = off) and set it from `RiskConfig` in the
study/CLI. Test: a target that sizes below the ticket produces no trade.

### F4 - MEDIUM: `custody_fee_annual` is configured but never charged

`CostConfig.custody_fee_annual` is required, documented as a real Saxo DK cost
(TODO-VERIFY), doubled by the stress line - and silently ignored by the engine.
If the verified value is nonzero, the study understates costs and "costs x2"
understates the stress. Prescription: accrue it in the engine - deduct
`custody_fee_annual/252 x market value of holdings` from cash at each session
mark (cash-only days accrue nothing). Test with a nonzero fee against a
hand-computed year. If the owner's price-list check confirms Saxo DK charges no
custody fee for this account class, set it to 0 in config and document that the
field is charged-on-holdings when nonzero.

### F5 - MEDIUM (process, not code): the real study must use a real FX series

`run_research.py` defaults to a constant 6.9 USD/DKK. COSTS_AND_TAX.md makes FX
movement part of the taxable gain; a constant erases that component and skews
after-tax results. The `--fx-csv` path exists. Prescription: the Gate 3G run
MUST pass `--fx-csv` with the daily DKK-per-USD series (Danmarks Nationalbank
publishes it); record the source in REPORT.md. Also validate the CSV shape
(two columns, parseable dates, positive rates) instead of `iloc` guessing.

### F6 - LOW: report verdict treats a missing stress study as a failed one

`build_report` marks "Cost x2 +" as `no` both when the doubled-cost study shows
a loss AND when it simply was not run (`--no-stress`). A candidate can appear to
fail Gate 3G because a variant is absent. Distinguish "not run" (render `n/a`
and exclude from the PASS conjunction, or refuse to render a verdict without the
stress line - I prefer refusing, since the stress line is a binding criterion).

### F7 - LOW: time-stop off-by-one in Candidate A

`days_held` counts sessions strictly after the entry-fill session, and the exit
fills at the next open, so `max_hold = 5` produces a 6-session entry-to-exit
span. Harmless for the grid (5 vs 8 stays a real contrast) but document the
convention in the docstring so the study's `max_hold` numbers are interpreted
correctly.

### F8 - LOW: `_pay_tax` can silently drive cash negative on total wipeout

`_raise_cash` can now liquidate everything (including delisting tails), so the
only remaining shortfall case is total equity < tax bill. In that case
`portfolio.cash_usd -= needed_usd` goes negative without a sound. Fail loud
(raise) - a backtest whose equity cannot cover its tax is a result the study
must surface, not absorb.

### F9 - INFO (documented v1 simplifications, restate in REPORT.md)

- Dividends ride in adjusted prices, so they are taxed as capital gains on sale
  rather than as dividend income in the year received, and the 15% US
  withholding credit path (`add_dividend`) is never exercised by the engine.
  Slightly flatters after-tax results for dividend payers; acceptable for v1,
  must be listed under REPORT.md assumptions.
- Universe tickers with punctuation (e.g. BRK.B) may not match yfinance's
  dash convention; `pull_data` skips fetch failures, silently shrinking the
  panel. The Phase 1 data-quality report should list skip counts; eyeball them
  before trusting the study.

## What is genuinely good (keep it this way)

- Engine timing discipline: decision-close sizing, next-open fills, gap stops
  before rebalance and intraday stops after, current-decision stops, no
  same-open re-entry, point-in-time universe enforcement - each with a test.
- Deferred-tax accounting (accrue at year-end, pay next year, size net of the
  liability, liquidate-to-pay without leverage) is the right shape for the
  realisation principle, and the golden buy-and-hold test pins it.
- The HOLD (`weight=None`) semantic is the correct minimal engine change for
  multi-day strategies; the churn test proves it.
- Research protocol: train-only selection, frozen OOS application, full-grid OOS
  for the plateau, append-only trials, DSR count and variance drawn from the
  same ledger, holdout reserved and never touched by the runner.
- 169 offline deterministic tests, CI-gated lint+format, ASCII-only source, no
  LLM anywhere near the trade path.

## Required sequence before Gate 3G

1. Phase 3c PR (Opus): F1 indicator precomputation, F2 spec amendment + C
   sizing, F3 min-ticket, F4 custody accrual, F6 verdict n/a-handling, F8 fail
   loud; plus the F7 docstring and the STRATEGY_SPEC.md sizing amendment.
2. Owner: pull data on an unrestricted machine; run the data-quality report and
   check skip counts (F9b); obtain the Nationalbanken USD/DKK series.
3. Owner: run the study with `--fx-csv` (F5); commit trials.csv + REPORT.md.
4. Fable + owner: read REPORT.md against the Gate 3G criteria. Holdout stays
   untouched until both explicitly approve.

## Resolution - Phase 3c (PR #17)

All code findings implemented and tested (180 tests, ruff clean):

- **F1 DONE** - `tradehelm/strategy/features.py` precomputes each causal indicator
  once per symbol; `StrategyContext.feature/value/sessions_since` do O(log n)
  point reads (cached row position); the study builds the feature panel once and
  hands it (plus `adjusted`) to every `BacktestEngine.run`. Measured: one
  backtest-year over 100 symbols went 84 s -> 2.3 s (~36x), precompute ~0.3 s per
  100 symbols. The full study drops from ~1,200 h (infeasible) to tens of hours
  (overnight, and subsettable via `--candidates`/`--limit`).
  `test_precomputed_features_match_prefix_recompute` locks equivalence to the
  prefix-recompute for all three candidates.
- **F2 DONE** - STRATEGY_SPEC.md amended; Candidate C sizes
  `min(1/n_hold, max_position_notional_frac)` via `EntrySignal.weight`.
- **F3 DONE** - `BacktestEngine(min_ticket_dkk=...)` skips sub-ticket buys; wired
  from `RiskConfig.min_ticket_dkk` through `RiskParams`.
- **F4 DONE** - `CostModel.custody_daily`; the engine accrues one session's
  custody on holdings value each session.
- **F5 DONE** - `run_research.py` `--fx-csv` is validated (two columns, parseable
  dates, positive rates) and sorted; the Gate 3G run must pass a real series.
- **F6 DONE** - the report renders `n/a` / "needs stress" when the doubled-cost
  line was not run, and does not call it a PASS/fail.
- **F7 DONE** - Candidate A docstring documents the `max_hold` entry-to-exit span.
- **F8 DONE** - `_pay_tax` raises when equity cannot cover the tax bill.
- **F9 DONE** - REPORT.md restates the dividend-as-capital-gains and ticker-format
  assumptions.
