# CLAUDE.md — development rules for Tradehelm

Read docs/PLAN.md before doing anything. Work happens in phase order; the current
phase is tracked in PLAN.md's status table.

## Commands

```
pip install -r requirements.txt          # backend deps
pytest                                   # all tests (must pass before any PR)
ruff check . && ruff format --check .    # lint
uvicorn tradehelm.api.app:app --reload   # dev API server (from Phase 5)
cd frontend && npm run dev               # dev frontend (from Phase 5)
```

Python 3.12, requirements.txt (no poetry/uv), pytest + ruff. Frontend: Vite +
React + TypeScript (from Phase 5 only).

## Workflow

- **One PR per phase** (sub-PRs within a phase are fine if large). Branch naming:
  `phase<N>-<slug>`. Never commit directly to main after the initial docs.
- Every PR: tests for new logic, `pytest` and `ruff` clean, short PR description
  listing which PLAN.md acceptance criteria it satisfies.
- After pushing a PR, request review with `@codex review`. For each review
  comment: fix, push, reply, resolve the thread. Merge only on clean approval.
- Do not start phase N+1 while phase N's PR is open.

## Hard rules

1. **Deterministic core.** No LLM/API-judgment call anywhere in `strategy/`,
   `risk/`, `backtest/`, `engine/`, or `broker/`. The `advisor/` module is the
   only place Claude API calls are allowed, and nothing in the trade path may
   read its output.
2. **SIM only.** `broker.environment` defaults to `sim`. Code that targets the
   live environment must sit behind an explicit `live: true` config flag AND a
   startup confirmation. Never set this flag yourself.
3. **No secrets in git.** AppKey/AppSecret/tokens live in `.env` (gitignored)
   and the local token store. Add `.env.example` with placeholder keys instead.
4. **No emoji or astral-plane Unicode in source files or logs.** ASCII plus
   Latin-1 only. (Prevents a known tooling issue on the owner's machine.)
5. **TODO-VERIFY protocol.** Numbers marked `TODO-VERIFY` in docs/config
   (Saxo fee rates, tax thresholds) are placeholders. Never silently treat them
   as confirmed; keep them in config, not hardcoded, and flag them in PR
   descriptions if a result depends on them.
6. **Tax and cost models are test-first.** Write the unit tests from the worked
   examples in docs/COSTS_AND_TAX.md before implementing.
7. **Trial registry.** Every backtest configuration run during research must be
   appended to `research/trials.csv` (strategy, params, period, net metrics).
   This feeds the deflated-Sharpe calculation and must never be pruned.
8. **Never fabricate market data.** If a data source fails, fail loudly; no
   synthetic fills, no interpolated bars presented as real.

## Style

- Match module conventions in docs/ARCHITECTURE.md (protocols + pydantic models).
- Small pure functions in strategy/cost/tax code; side effects live in
  engine/broker/storage.
- Docstrings state units and timezone for every time/price/money value.
  All internal money amounts are in the instrument's own currency; account
  equity is DKK; all timestamps UTC internally, Europe/Copenhagen in the UI.
