# research/

Outputs of the strategy study (docs/PLAN.md Phase 3, docs/STRATEGY_SPEC.md).

Generate them from the repo root, after populating the data cache
(`python -m scripts.pull_data --start 2005-01-01`):

```
python -m scripts.run_research --start 2005-01-01 --fx-csv usd_dkk.csv
```

## The USD/DKK series (`--fx-csv`)

The Gate 3G run MUST pass a real daily USD/DKK series: FX movement is part of the
Danish taxable gain, and a constant rate erases it (a constant is only for smoke
tests). Provenance and the one gotcha:

- Source: Danmarks Nationalbank, StatBank series **DNVALD** (daily exchange rates),
  the **USD** row. CSV shape: two columns, `date,rate`.
- Nationalbank quotes **kroner per 100 units** of foreign currency, so the raw USD
  figure is ~**550-900**. The engine wants **DKK per 1 USD (~5-9)** - so **divide
  the rate column by 100** before feeding it in.
- The runner enforces a plausibility band (3-15 DKK/USD) and fails loud, naming the
  divide-by-100 / invert mistakes, rather than silently rescaling. It also echoes
  the range actually in use to stderr and into `REPORT.md`'s data note - eyeball it.

- `trials.csv` - the trial registry. EVERY backtest configuration run during
  research (each grid point per train span, each OOS test run, each stress rerun)
  is appended here and MUST NEVER be pruned (CLAUDE.md rule 8) - its row count is
  the trial count the deflated Sharpe discounts against.
- `REPORT.md` - the study feeding Gate 3G: per-window OOS results net of costs AND
  Danish tax, the parameter-plateau surface, the after-tax SPY benchmark, the
  doubled-cost and 27%-flat-tax stress lines, the deflated Sharpe, and a
  preliminary pass/fail per candidate.
- `plots/` - optional generated figures (gitignored; regenerated from trials.csv).

The harness lives in `tradehelm/research/`; the runner is `scripts/run_research.py`.
The final untouched **holdout** is one-shot, NOT produced by the study run - it is
gated on owner + reviewer approval (Gate 3G) and run separately.
