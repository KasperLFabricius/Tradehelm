# Tradehelm — Strategy Specification (v1 candidates)

Three rule families, chosen because each has a documented economic rationale,
works on daily bars (fits decision D4), and spans the turnover spectrum — so
Gate 3G learns how much turnover Danish costs+tax can survive, not just whether
one idea works. All long-only in v1.

Universe for all candidates: S&P 500 members **as of the decision date**
(point-in-time), further filtered to price > 5 USD and 20-day median dollar
volume > 10M USD.

## Candidate A — trend-filtered pullback (mean reversion, highest turnover)

Rationale: short-term reversal in liquid large caps within established uptrends.
Expected hold 2-6 days.

- Regime filter: instrument close > SMA(200); SPY close > SMA(200).
- Entry signal at T close: RSI(2) < `rsi_entry` AND close < SMA(5).
- Entry: market at T+1 open.
- Exit signal: close > SMA(5) OR RSI(2) > 70 -> exit at next open.
- Time stop: `max_hold` trading days -> exit at next open.
- Protective stop (resting GTC): entry_fill - `stop_atr` * ATR(14).
- Ranking when signals > free slots: lowest RSI(2) first.

Parameter grid: `rsi_entry` in {5, 10, 15}; `stop_atr` in {2.0, 3.0};
`max_hold` in {5, 8}. 12 combinations.

## Candidate B — breakout continuation (medium turnover)

Rationale: momentum continuation after range expansion. Expected hold 1-8 weeks
(slower than A; still swing by decision cadence).

- Regime filter: SPY close > SMA(200).
- Entry signal at T close: close = highest close of the last `entry_lookback` days.
- Entry: market at T+1 open.
- Exit: close < lowest close of last `exit_lookback` days -> next open; plus a
  trailing stop at `stop_atr` * ATR(14) below the highest close since entry
  (stop replaced at broker on each EOD cycle).
- Ranking: highest 100-day return first.

Parameter grid: `entry_lookback` in {20, 55}; `exit_lookback` in {10, 20};
`stop_atr` in {2.0, 3.0}. 8 combinations.

## Candidate C — weekly cross-sectional momentum rotation (lowest turnover, tax-friendliest)

Rationale: classic relative-strength effect; deliberately included as the
low-cost/low-tax-drag comparator and likely survivor.

- Decision only on the last trading day of each week.
- Score: blended return = 0.5 * r(126d) + 0.5 * r(252d), both skipping the most
  recent 5 days.
- Hold the top `n_hold` names; sell a holding only when it drops out of the top
  `n_hold * buffer` ranks (buffer reduces churn).
- Regime filter: SPY close > SMA(200), else move to cash.
- Protective stop: 20% below entry (catastrophe stop only).

Parameter grid: `n_hold` in {3, 5}; `buffer` in {1.5, 2.0}. 4 combinations.

## Position sizing (all candidates, shared risk layer)

Candidates A and B (risk-based — their ATR stops are genuine risk anchors):
shares = (equity * 1%) / (entry - stop). Capped by max-notional and
max-positions rules in ARCHITECTURE.md section 5.

Candidate C (amended 2026-07-03, Fable review F2): each holding is sized at
min(1/n_hold, max_position_notional_frac) of equity. The 20% catastrophe stop is
an exit only and does NOT enter the sizing formula — sizing a rotation strategy
off a disaster stop would leave it ~75% cash and invalidate it as the
low-turnover comparator.

All candidates: fractional shares are not assumed; round down; skip if resulting
notional < `min_ticket` (config, default 2,000 DKK equivalent — below this,
minimum commission drag exceeds 0.5% per side).

## Validation protocol (binding)

1. Data: 2005-01-01 to present, daily, survivorship-handled universe.
2. Walk-forward: train 3y / test 1y, rolled annually, 5-trading-day purge at
   each boundary. Parameters selected on train only (best net-of-everything
   Sharpe), applied frozen to the test year.
3. Final holdout: most recent 2 years, untouched during all research, run once
   after Gate-3G preliminary approval, with the walk-forward-selected procedure.
4. All results reported net of costs AND Danish tax per COSTS_AND_TAX.md, plus
   a pre-tax line for diagnosis only.
5. Every configuration executed is appended to research/trials.csv; deflated
   Sharpe uses the full trial count (all candidates, all grid points, all reruns).
6. Benchmark: SPY buy-and-hold through the same cost+tax engine.
7. Robustness: report OOS metric as a function of each parameter (plateau
   check); report results with costs doubled (stress line).
8. Sensitivity to the 42% bracket: report after-tax results at both 27% flat
   (small-scale years) and blended 27/42 (scaled years).

## Pass criteria (feeds Gate 3G, PLAN.md)

- OOS net-of-everything risk-adjusted return > after-tax SPY benchmark.
- Positive net result in >= 60% of OOS windows.
- Parameter plateau (no single-point spikes).
- Survives the doubled-costs stress line with positive expectancy.
- Holdout consistent with walk-forward expectation band.

## Explicitly out of scope for v1

Shorting; leverage; intraday signals; neural networks / deep RL; LLM-derived
features (may be *researched* later as cached, versioned, backtestable features
— never as a live judgment call); options; non-USD universes (OMXC25 is a
documented later extension once the pipeline is proven).
