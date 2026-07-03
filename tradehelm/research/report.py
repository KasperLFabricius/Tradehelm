"""Render research/REPORT.md from the study results (docs/STRATEGY_SPEC.md).

Everything is net of costs AND Danish tax. The report is the artifact Gate 3G reads;
it computes the deflated Sharpe over the true trial count, tabulates the per-window
out-of-sample results and the parameter-plateau surface, compares against after-tax
SPY buy-and-hold, folds in the doubled-cost and 27%-flat stress lines, and states a
PRELIMINARY pass/fail per the binding criteria (the one-shot holdout stays pending
owner+reviewer approval). ASCII only.
"""

from __future__ import annotations

from statistics import pvariance

import pandas as pd

from ..backtest import metrics
from .study import CandidateStudy
from .trials import TrialLog

BASE = (1.0, "27/42")  # cost_mult, tax_label of the headline variant
POSITIVE_WINDOW_THRESHOLD = 0.60


def _pct(x) -> str:
    return f"{x * 100:.1f}%" if isinstance(x, (int, float)) else "-"


def _num(x, nd: int = 2) -> str:
    return f"{x:.{nd}f}" if isinstance(x, (int, float)) else "-"


def _get(d: dict, key: str, default=0.0):
    return d.get(key, default) if d else default


def _dsr_for(study: CandidateStudy, sr_variance: float, n_trials: int) -> float:
    rets = study.combined_oos_returns
    if rets is None or len(rets) < 3:
        return 0.0
    # Seed with the initial capital (1.0) so deflated_sharpe_ratio's pct_change keeps
    # the FIRST OOS return instead of dropping it (it would otherwise be lost).
    curve = (1.0 + rets.sort_index()).cumprod()
    equity = pd.Series([1.0, *curve.tolist()])
    return metrics.deflated_sharpe_ratio(equity, sr_variance, n_trials)


def _index_studies(studies: list[CandidateStudy]) -> dict[tuple[str, float, str], CandidateStudy]:
    return {(s.candidate, s.cost_mult, s.tax_label): s for s in studies}


def _ledger_trial_stats(trials: TrialLog) -> tuple[int, float]:
    """(trial count, variance of per-period Sharpes) over the ENTIRE ledger, so the
    deflated Sharpe's N and its SR* variance describe the same trial set."""
    if trials.count() == 0:
        return 0, 0.0
    df = trials.read()
    n = len(df)
    srs = [float(x) for x in df.get("sharpe_pp", pd.Series(dtype=float)).dropna()]
    return n, (pvariance(srs) if len(srs) > 1 else 0.0)


def build_report(
    studies: list[CandidateStudy],
    trials: TrialLog,
    *,
    data_note: str = "",
    generated: str | None = None,
) -> str:
    generated = generated or str(pd.Timestamp.now().date())
    by_key = _index_studies(studies)
    base_studies = [s for s in studies if (s.cost_mult, s.tax_label) == BASE]

    # The deflated Sharpe must draw its trial COUNT and its Sharpe VARIANCE from the
    # SAME set - the whole append-only ledger, including stress rows and earlier
    # reruns the CLI appended - or SR* would mix a full-ledger N with a partial
    # variance and mis-state significance (Codex PR #14 review).
    n_trials, sr_variance = _ledger_trial_stats(trials)

    lines: list[str] = []
    lines.append("# Tradehelm - Strategy Research Report (v1 candidates)")
    lines.append("")
    lines.append(f"Generated: {generated}. All figures are NET of Saxo costs AND Danish")
    lines.append("aktieindkomst tax (see docs/COSTS_AND_TAX.md), unless a row is labelled pre-tax.")
    if data_note:
        lines.append("")
        lines.append(data_note)
    lines.append("")
    lines.append(f"- Trials executed (research/trials.csv, never pruned): **{n_trials}**")
    lines.append(f"- Deflated-Sharpe trial variance (per-period SR): {_num(sr_variance, 5)}")
    lines.append("- Deflated Sharpe = P(true Sharpe > the best-of-N-trials luck threshold).")
    lines.append("")

    # ---- Preliminary Gate 3G verdict ---------------------------------------
    verdicts: dict[str, bool] = {}
    lines.append("## Gate 3G - preliminary verdict")
    lines.append("")
    lines.append(
        "| Candidate | OOS Sharpe | SPY Sharpe | Beats SPY | % windows + | Cost x2 + | "
        "Deflated Sharpe | Prelim |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for s in base_studies:
        stress = by_key.get((s.candidate, 2.0, "27/42"))
        oos_sr = _get(s.combined_oos, "sharpe")
        spy_sr = _get(s.combined_benchmark, "sharpe")
        beats = oos_sr > spy_sr
        windows_ok = s.pct_positive_windows >= POSITIVE_WINDOW_THRESHOLD
        stress_ok = bool(stress) and _get(stress.combined_oos, "total_return") > 0.0
        dsr = _dsr_for(s, sr_variance, n_trials)
        prelim = beats and windows_ok and stress_ok
        verdicts[s.candidate] = prelim
        lines.append(
            f"| {s.candidate} | {_num(oos_sr)} | {_num(spy_sr)} | {'yes' if beats else 'no'} | "
            f"{_pct(s.pct_positive_windows)} | {'yes' if stress_ok else 'no'} | "
            f"{_num(dsr, 3)} | {'PASS' if prelim else 'fail'} |"
        )
    lines.append("")
    passers = [c for c, ok in verdicts.items() if ok]
    if passers:
        lines.append(
            f"**{len(passers)} candidate(s) pass the automatable criteria: "
            f"{', '.join(passers)}.** The single-shot holdout remains PENDING explicit "
            "owner + reviewer approval (STRATEGY_SPEC.md); it has not been run."
        )
    else:
        lines.append(
            "**No candidate passes the preliminary criteria.** Per PLAN.md Gate 3G the "
            "project stops here or returns to research - do NOT run the holdout, and do "
            "not build Phases 4-8 (they add no alpha)."
        )
    lines.append("")

    # ---- Per-candidate detail ----------------------------------------------
    for s in base_studies:
        lines.extend(_candidate_section(s, by_key))

    lines.append("## Notes")
    lines.append("")
    lines.append(
        "- Parameter plateau is reported as a surface; confirm by eye that the selected "
        "region is a plateau, not an isolated spike (a spike fails Gate 3G even if the "
        "headline metrics pass)."
    )
    lines.append("- Holdout is one-shot: running it consumes it. It is not run by this report.")
    lines.append("")
    return "\n".join(lines)


def _candidate_section(s: CandidateStudy, by_key: dict) -> list[str]:
    out: list[str] = []
    out.append(f"## {s.candidate}")
    out.append("")
    out.append("Combined out-of-sample (stitched across all test windows), net of everything:")
    out.append("")
    out.append("| Line | Total return | CAGR | Sharpe | Max drawdown |")
    out.append("|---|---|---|---|---|")
    out.append(_metric_row("OOS (strategy)", s.combined_oos))
    out.append(_metric_row("SPY buy-and-hold", s.combined_benchmark))
    cost2 = by_key.get((s.candidate, 2.0, "27/42"))
    if cost2:
        out.append(_metric_row("OOS, costs x2", cost2.combined_oos))
    tax_flat = by_key.get((s.candidate, 1.0, "27flat"))
    if tax_flat:
        out.append(_metric_row("OOS, 27% flat tax", tax_flat.combined_oos))
    out.append("")
    out.append(f"Positive OOS windows: **{_pct(s.pct_positive_windows)}**")
    out.append("")

    # Per-window OOS
    out.append("### Per-window out-of-sample")
    out.append("")
    out.append("| # | Test span | Selected params | OOS return | OOS Sharpe | SPY return |")
    out.append("|---|---|---|---|---|---|")
    for w in s.windows:
        params = ";".join(f"{k}={w.selected_params[k]}" for k in sorted(w.selected_params))
        out.append(
            f"| {w.window} | {w.test_start}..{w.test_end} | {params} | "
            f"{_pct(_get(w.oos, 'total_return'))} | {_num(_get(w.oos, 'sharpe'))} | "
            f"{_pct(_get(w.benchmark, 'total_return'))} |"
        )
    out.append("")

    # Plateau
    out.append("### Parameter plateau (mean OOS Sharpe by value)")
    out.append("")
    for pname, buckets in s.plateau.items():
        cells = ", ".join(f"{v}: {_num(sr)}" for v, sr in buckets.items())
        out.append(f"- **{pname}** - {cells}")
    out.append("")
    return out


def _metric_row(label: str, m: dict) -> str:
    return (
        f"| {label} | {_pct(_get(m, 'total_return'))} | {_pct(_get(m, 'cagr'))} | "
        f"{_num(_get(m, 'sharpe'))} | {_pct(_get(m, 'max_drawdown'))} |"
    )
