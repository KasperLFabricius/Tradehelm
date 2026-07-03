"""Walk-forward research study for the v1 candidates (docs/STRATEGY_SPEC.md).

For each candidate and each stress variant (base, doubled costs, 27%-flat tax) it
runs the binding protocol: on every walk-forward window it evaluates the full
parameter grid on the TRAIN span, freezes the best (net-of-everything Sharpe), and
scores the whole grid on the TEST span - the selected point is the out-of-sample
result, the whole grid gives the parameter-plateau surface. Every run is appended to
the trials ledger. Nothing here fetches data; a caller supplies the price panel, so
the study is deterministic and unit-testable on synthetic panels.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import pandas as pd

from ..backtest import metrics
from ..backtest.costs import CostModel
from ..backtest.engine import BacktestEngine, BacktestResult, adjusted_ohlc
from ..backtest.walkforward import walk_forward_windows
from ..config.models import CostConfig
from ..strategy import BuyAndHold, CandidateA, CandidateB, CandidateC, RiskParams
from ..strategy.features import build_features
from .trials import Trial, TrialLog, params_str

# Parameter grids exactly as specified in docs/STRATEGY_SPEC.md.
PARAM_GRIDS: dict[str, list[dict]] = {
    "candidate_a": [
        {"rsi_entry": r, "stop_atr": s, "max_hold": h}
        for r, s, h in itertools.product((5.0, 10.0, 15.0), (2.0, 3.0), (5, 8))
    ],
    "candidate_b": [
        {"entry_lookback": e, "exit_lookback": x, "stop_atr": s}
        for e, x, s in itertools.product((20, 55), (10, 20), (2.0, 3.0))
    ],
    "candidate_c": [{"n_hold": n, "buffer": b} for n, b in itertools.product((3, 5), (1.5, 2.0))],
}

_FACTORIES = {"candidate_a": CandidateA, "candidate_b": CandidateB, "candidate_c": CandidateC}


def make_candidate(name: str, params: dict, calendar, risk: RiskParams):
    """A fresh candidate instance (fresh PositionBook) for the given parameters."""
    if name == "candidate_c":
        return CandidateC(risk=risk, calendar=calendar, **params)
    return _FACTORIES[name](risk=risk, **params)


def doubled_costs(cfg: CostConfig) -> CostConfig:
    """A CostConfig with every cost input doubled (the protocol's stress line)."""
    return CostConfig(
        commission_rate_us=min(1.0, cfg.commission_rate_us * 2),
        min_commission_us=cfg.min_commission_us * 2,
        half_spread_bps=cfg.half_spread_bps * 2,
        slippage_bps=cfg.slippage_bps * 2,
        fx_conversion_rate=min(1.0, cfg.fx_conversion_rate * 2),
        custody_fee_annual=min(1.0, cfg.custody_fee_annual * 2),
    )


@dataclass
class WindowOOS:
    window: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    selected_params: dict
    oos: dict  # metrics.summary of the selected OOS run
    benchmark: dict  # metrics.summary of buy-and-hold over the same test span


@dataclass
class CandidateStudy:
    candidate: str
    cost_mult: float
    tax_label: str
    windows: list[WindowOOS]
    combined_oos: dict  # stitched across all OOS test spans
    combined_benchmark: dict
    pct_positive_windows: float
    plateau: dict[str, dict[str, float]]  # param -> {value: mean OOS annualized Sharpe}
    combined_oos_returns: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    per_period_srs: list[float] = field(default_factory=list)  # DSR variance source
    n_configs: int = 0


def summarize_returns(rets: pd.Series) -> dict:
    """Metrics from a concatenated daily-return series (stitched OOS windows)."""
    rets = rets.sort_index()
    if len(rets) < 2:
        return {"total_return": 0.0, "cagr": 0.0, "sharpe": 0.0, "max_drawdown": 0.0, "n_obs": 0}
    curve = (1.0 + rets).cumprod()
    total = float(curve.iloc[-1] - 1.0)
    days = (rets.index[-1] - rets.index[0]).days
    years = days / 365.25 if days > 0 else 0.0
    cagr = float((1.0 + total) ** (1.0 / years) - 1.0) if years > 0 else 0.0
    std = rets.std(ddof=1)
    sharpe = float(rets.mean() / std * (metrics.TRADING_DAYS**0.5)) if std else 0.0
    # Seed the curve with the initial capital level (1.0) so a drawdown from the very
    # first day is measured against the start, not against the first post-return value.
    seeded = pd.Series([1.0, *curve.tolist()])
    max_dd = float((seeded / seeded.cummax() - 1.0).min())
    return {
        "total_return": total,
        "cagr": cagr,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "n_obs": int(len(rets)),
    }


def _trial(name, phase, wi, params, cost_mult, tax_label, span_start, span_end, res) -> Trial:
    eq = res.equity_dkk
    summ = metrics.summary(eq)
    return Trial(
        candidate=name,
        phase=phase,
        window=wi,
        params=params_str(params),
        cost_mult=cost_mult,
        tax_label=tax_label,
        span_start=str(pd.Timestamp(span_start).date()),
        span_end=str(pd.Timestamp(span_end).date()),
        n_obs=int(len(metrics.daily_returns(eq))),
        sharpe=summ["sharpe"],
        sharpe_pp=metrics.sharpe_per_period(eq),
        cagr=summ["cagr"],
        max_drawdown=summ["max_drawdown"],
        total_return=summ["total_return"],
        final_equity_dkk=float(res.final_equity_dkk),
    )


def _plateau(grid: list[dict], grid_test: dict[str, dict]) -> dict[str, dict[str, float]]:
    """Mean OOS annualized Sharpe grouped by each single parameter's value."""
    param_names = sorted({k for p in grid for k in p})
    out: dict[str, dict[str, float]] = {}
    for pname in param_names:
        buckets: dict[str, list[float]] = {}
        for entry in grid_test.values():
            val = str(entry["params"].get(pname))
            buckets.setdefault(val, []).extend(entry["sr"])
        out[pname] = {
            v: (sum(lst) / len(lst) if lst else 0.0) for v, lst in sorted(buckets.items())
        }
    return out


def run_candidate_study(
    name: str,
    panel: dict[str, pd.DataFrame],
    members_fn,
    calendar,
    cost_model: CostModel,
    tax_thresholds: dict[int, float],
    start,
    end,
    initial_dkk: float,
    usd_dkk,
    risk: RiskParams,
    trials: TrialLog,
    *,
    benchmark_symbol: str = "SPY",
    cost_mult: float = 1.0,
    tax_label: str = "27/42",
    rate_low: float = 0.27,
    rate_high: float = 0.42,
    train_years: int = 3,
    test_years: int = 1,
    purge_sessions: int = 5,
    holdout_years: int = 2,
) -> CandidateStudy:
    windows = walk_forward_windows(
        start, end, train_years, test_years, purge_sessions, holdout_years, calendar
    )
    grid = PARAM_GRIDS[name]

    # Precompute adjusted bars + indicator features ONCE for the whole study; every
    # backtest below reuses them, so the O(days^2) indicator work is not repeated per
    # run (Fable review F1). The engine rebuilds nothing when handed these.
    adjusted = {sym: adjusted_ohlc(df) for sym, df in panel.items()}
    features = build_features(adjusted)

    def bench_members(_day):
        return [benchmark_symbol]  # single-symbol universe so the engine keeps the SPY target

    def run(strategy, s, e, members=members_fn) -> BacktestResult:
        engine = BacktestEngine(
            calendar,
            cost_model,
            tax_thresholds,
            rate_low=rate_low,
            rate_high=rate_high,
            min_ticket_dkk=risk.min_ticket_dkk,
        )
        return engine.run(
            strategy,
            panel,
            members,
            s,
            e,
            initial_dkk,
            usd_dkk,
            adjusted=adjusted,
            features=features,
        )

    per_period_srs: list[float] = []
    grid_test: dict[str, dict] = {params_str(p): {"params": p, "sr": []} for p in grid}
    window_rows: list[WindowOOS] = []
    positive = 0
    oos_returns: list[pd.Series] = []
    bench_returns: list[pd.Series] = []

    for wi, w in enumerate(windows):
        # TRAIN: score the whole grid, freeze the best net-of-everything Sharpe.
        best_key, best_sr = None, float("-inf")
        for p in grid:
            res = run(make_candidate(name, p, calendar, risk), w.train_start, w.train_end)
            trials.append(
                _trial(name, "train", wi, p, cost_mult, tax_label, w.train_start, w.train_end, res)
            )
            per_period_srs.append(metrics.sharpe_per_period(res.equity_dkk))
            sr = metrics.sharpe(res.equity_dkk)
            if sr > best_sr:
                best_sr, best_key = sr, params_str(p)

        # TEST: score the whole grid OOS (plateau); the frozen point is the OOS result.
        selected: BacktestResult | None = None
        selected_params: dict = {}
        for p in grid:
            res = run(make_candidate(name, p, calendar, risk), w.test_start, w.test_end)
            trials.append(
                _trial(name, "test", wi, p, cost_mult, tax_label, w.test_start, w.test_end, res)
            )
            grid_test[params_str(p)]["sr"].append(metrics.sharpe(res.equity_dkk))
            if params_str(p) == best_key:
                selected, selected_params = res, p

        bench = run(BuyAndHold(benchmark_symbol), w.test_start, w.test_end, members=bench_members)

        assert selected is not None  # best_key always corresponds to a grid point
        oos_returns.append(metrics.daily_returns(selected.equity_dkk))
        bench_returns.append(metrics.daily_returns(bench.equity_dkk))
        oos_summary = metrics.summary(selected.equity_dkk)
        if oos_summary["total_return"] > 0:
            positive += 1
        window_rows.append(
            WindowOOS(
                window=wi,
                train_start=str(w.train_start.date()),
                train_end=str(w.train_end.date()),
                test_start=str(w.test_start.date()),
                test_end=str(w.test_end.date()),
                selected_params=selected_params,
                oos=oos_summary,
                benchmark=metrics.summary(bench.equity_dkk),
            )
        )

    stitched = pd.concat(oos_returns).sort_index() if oos_returns else pd.Series(dtype=float)
    combined_oos = summarize_returns(stitched) if len(stitched) else {}
    combined_bench = summarize_returns(pd.concat(bench_returns)) if bench_returns else {}
    return CandidateStudy(
        candidate=name,
        cost_mult=cost_mult,
        tax_label=tax_label,
        windows=window_rows,
        combined_oos=combined_oos,
        combined_benchmark=combined_bench,
        pct_positive_windows=(positive / len(windows)) if windows else 0.0,
        plateau=_plateau(grid, grid_test),
        combined_oos_returns=stitched,
        per_period_srs=per_period_srs,
        n_configs=len(grid),
    )
