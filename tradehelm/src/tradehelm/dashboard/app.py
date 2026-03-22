"""Streamlit Strategy Lab dashboard."""
from __future__ import annotations

import datetime as dt

import streamlit as st

from tradehelm.dashboard.client import ApiResult, call_api

DEFAULT_API = "http://127.0.0.1:8000"


def notify_action(result: ApiResult, ok_msg: str) -> None:
    if result.ok:
        st.success(ok_msg)
    else:
        st.error(result.error or "Action failed")


def _default_request() -> dict:
    today = dt.date.today()
    return {
        "symbols": ["AAPL", "MSFT"],
        "start_date": (today - dt.timedelta(days=20)).isoformat(),
        "end_date": (today - dt.timedelta(days=1)).isoformat(),
        "interval": "5min",
        "adjusted": True,
        "enabled_strategies": ["orb", "vwap"],
        "strategy_params": {},
    }


def render() -> None:
    st.title("TradeHelm Strategy Lab")
    st.caption("Live experiment queue, run analysis, and strategy comparison.")

    st.sidebar.text_input("Control API URL", key="api_url", value=DEFAULT_API)
    api = st.session_state.api_url

    catalog_result = call_api(api, "GET", "/backtests/strategies/catalog")
    catalog = catalog_result.payload if catalog_result.ok and isinstance(catalog_result.payload, list) else []
    intervals = call_api(api, "GET", "/historical/intervals").payload or {"intervals": ["5min"], "default": "5min"}

    st.header("New Experiment")
    defaults = _default_request()
    tickers = st.text_input("Tickers", value=",".join(defaults["symbols"]))
    c1, c2, c3 = st.columns(3)
    start_date = c1.date_input("Start", value=dt.date.fromisoformat(defaults["start_date"]))
    end_date = c2.date_input("End", value=dt.date.fromisoformat(defaults["end_date"]))
    interval = c3.selectbox("Interval", intervals.get("intervals", ["5min"]))
    adjusted = st.toggle("Adjusted", value=True)

    strategy_ids = [row.get("strategy_id") for row in catalog if isinstance(row, dict)]
    enabled_strategies = st.multiselect("Enabled strategies", strategy_ids, default=[s for s in ["orb", "vwap"] if s in strategy_ids])
    strategy_params: dict[str, dict] = {}
    with st.expander("Per-strategy parameters", expanded=False):
        for row in catalog:
            if not isinstance(row, dict):
                continue
            sid = row.get("strategy_id")
            defaults_map = row.get("defaults") or {}
            st.markdown(f"**{row.get('display_name')}** — {row.get('description')}")
            patch: dict = {}
            for k, v in defaults_map.items():
                if k == "enabled":
                    continue
                key = f"{sid}_{k}"
                if isinstance(v, bool):
                    patch[k] = st.checkbox(f"{sid}.{k}", value=v, key=key)
                elif isinstance(v, int):
                    patch[k] = int(st.number_input(f"{sid}.{k}", value=v, key=key))
                elif isinstance(v, float):
                    patch[k] = float(st.number_input(f"{sid}.{k}", value=float(v), key=key))
                else:
                    patch[k] = st.text_input(f"{sid}.{k}", value=str(v), key=key)
            strategy_params[sid] = patch

    with st.expander("Optional friction/risk overrides", expanded=False):
        risk_override = st.number_input("risk.max_trades_per_day override (0=off)", min_value=0, value=0)
        friction_override = st.number_input("friction.assumed_slippage_bps override (0=off)", min_value=0.0, value=0.0)

    symbols = [s.strip().upper() for s in tickers.split(",") if s.strip()]
    request_payload = {
        "symbols": symbols,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "interval": interval,
        "adjusted": adjusted,
        "enabled_strategies": enabled_strategies,
        "strategy_params": {sid: strategy_params.get(sid, {}) for sid in enabled_strategies},
        "risk_overrides": {"max_trades_per_day": int(risk_override)} if risk_override > 0 else None,
        "friction_overrides": {"assumed_slippage_bps": float(friction_override)} if friction_override > 0 else None,
    }

    b1, b2 = st.columns(2)
    if b1.button("Fetch/cache if needed"):
        notify_action(call_api(api, "POST", "/historical/fetch", {**request_payload, "use_existing_cache": True}), "Historical data fetched/cached.")
    if b2.button("Queue backtest job"):
        notify_action(call_api(api, "POST", "/backtests/jobs", request_payload), "Backtest job queued.")

    st.header("Running Jobs")
    if hasattr(st, "fragment"):
        @st.fragment(run_every="2s")
        def jobs_fragment() -> None:
            jobs = call_api(api, "GET", "/backtests/jobs").payload or []
            st.dataframe(jobs)
        jobs_fragment()
    else:
        st.dataframe(call_api(api, "GET", "/backtests/jobs").payload or [])

    jobs = call_api(api, "GET", "/backtests/jobs").payload or []
    job_map = {f"Job {j['id']} ({j['status']})": j["id"] for j in jobs if isinstance(j, dict) and "id" in j}

    st.header("Job progress panel")
    if job_map:
        sel_job = st.selectbox("Select job", list(job_map.keys()))
        job_id = job_map[sel_job]
        job = call_api(api, "GET", f"/backtests/jobs/{job_id}").payload or {}
        st.json(job.get("progress", {}))
        if st.button("Cancel selected job"):
            notify_action(call_api(api, "POST", f"/backtests/jobs/{job_id}/cancel"), "Cancel request submitted.")

        st.subheader("Event feed panel")
        st.dataframe(call_api(api, "GET", f"/backtests/jobs/{job_id}/events").payload or [])

    st.header("Run Detail panel")
    runs = call_api(api, "GET", "/backtests/runs").payload or []
    run_map = {f"Run {r['id']} ({r['status']})": r["id"] for r in runs if isinstance(r, dict) and "id" in r}
    if run_map:
        run_id = run_map[st.selectbox("Select run", list(run_map.keys()))]
        detail = call_api(api, "GET", f"/backtests/{run_id}").payload or {}
        st.write("Config snapshot")
        st.json(detail.get("config", {}))
        st.write("Summary")
        st.json(detail.get("summary", {}))
        st.write("Equity curve")
        st.line_chart(detail.get("equity_curve", []), x="timestamp", y="equity")
        st.write("Per-symbol summary")
        st.dataframe(detail.get("symbol_summary", []))
        st.write("Per-strategy summary")
        st.dataframe(detail.get("strategy_summary", []))
        st.write("Decision summary")
        st.json(detail.get("decision_summary", {}))
        st.write("Trade timeline")
        st.dataframe(detail.get("trade_timeline", []))

    st.header("Run Comparison panel")
    if run_map:
        selected = st.multiselect("Select 2+ runs", list(run_map.keys()))
        if st.button("Compare") and len(selected) >= 2:
            ids = [run_map[x] for x in selected]
            cmp = call_api(api, "POST", "/backtests/compare", {"run_ids": ids})
            if cmp.ok and isinstance(cmp.payload, dict):
                st.dataframe(cmp.payload.get("runs", []))


render()
