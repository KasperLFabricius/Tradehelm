# research/

Outputs of the strategy study (docs/PLAN.md Phase 3, docs/STRATEGY_SPEC.md).

Generate them from the repo root, after populating the data cache
(`python -m scripts.pull_data --start 2005-01-01`):

```
python -m scripts.run_research --start 2005-01-01
```

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
