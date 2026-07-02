# research/

Research artifacts for the strategy study (Phase 3).

- `trials.csv` - the trial registry. EVERY backtest configuration run during
  research is appended here (strategy, params, period, net metrics). It feeds the
  deflated-Sharpe calculation and MUST NEVER be pruned (CLAUDE.md rule 7). Created
  in Phase 2/3 once the metric columns are defined.
- `REPORT.md` - the written study feeding Gate 3G (results net of costs AND tax,
  parameter-plateau plots, per-window OOS table). Created in Phase 3.
- `plots/` - generated figures (gitignored; regenerated from trials.csv).

Nothing here yet; this file marks the directory and its intended contents.
