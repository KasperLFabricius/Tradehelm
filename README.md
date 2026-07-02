# Tradehelm

Autonomous swing-trading system for Saxo Bank (OpenAPI), built for a Danish retail
investor. Deterministic, backtestable strategy core; web UI for monitoring and
control; optional LLM-generated advisory briefings (never in the trade path).

**Status: planning complete, implementation not started.**

## Documents

| Doc | Purpose |
|---|---|
| [docs/PLAN.md](docs/PLAN.md) | Roadmap, phases, gates, acceptance criteria |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, module contracts, runtime model |
| [docs/STRATEGY_SPEC.md](docs/STRATEGY_SPEC.md) | Candidate strategies, exact rules, validation protocol |
| [docs/COSTS_AND_TAX.md](docs/COSTS_AND_TAX.md) | Cost model and Danish tax model specification |
| [CLAUDE.md](CLAUDE.md) | Development workflow rules for the implementing agent |

## Non-negotiable principles

1. **Deterministic core.** No LLM call ever influences a trading decision.
   The strategy must be exactly reproducible from historical data.
2. **Costs and tax first.** Every reported backtest number is net of commission,
   spread, slippage, FX, and Danish tax. Pre-cost results are not results.
3. **Phase gates.** No phase starts before the previous phase's PR is reviewed
   and its acceptance criteria pass. Live trading requires passing every gate.
4. **SIM by default.** The Saxo live environment is unreachable unless explicitly
   configured, and Phase 7's checklist has been signed off by the owner.
