"""Streamlit operator dashboard for TradeHelm."""
from __future__ import annotations

import streamlit as st

from tradehelm.dashboard.client import ApiResult, call_api

DEFAULT_API = "http://127.0.0.1:8000"


def notify_action(result: ApiResult, ok_msg: str) -> None:
    if result.ok:
        st.success(ok_msg)
    else:
        st.error(result.error or "Action failed")


def render_live_status(api: str) -> None:
    health = call_api(api, "GET", "/health")
    state = call_api(api, "GET", "/state")

    if not health.ok or not state.ok:
        st.error((health.error or "") + " " + (state.error or ""))
        return

    health_payload = health.payload or {}
    state_payload = state.payload or {}
    readiness = health_payload.get("readiness", {})

    st.header("Command Center")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Mode", state_payload.get("mode", "UNKNOWN"))
    c2.metric("Replay Loaded", str(readiness.get("replay_loaded", False)))
    c3.metric("Replay Running", str(readiness.get("replay_running", False)))
    c4.metric("DB Reachable", str(readiness.get("db_reachable", False)))

    st.caption(f"Replay Path: {state_payload.get('replay_path')}")
    st.caption(f"Started: {state_payload.get('replay_started_at')} | Completed: {state_payload.get('replay_completed_at')}")


def render() -> None:
    st.title("TradeHelm Dashboard")

    if "api_url" not in st.session_state:
        st.session_state.api_url = DEFAULT_API
    if "refresh_seconds" not in st.session_state:
        st.session_state.refresh_seconds = 2
    if "auto_refresh" not in st.session_state:
        st.session_state.auto_refresh = True

    st.sidebar.text_input("Control API URL", key="api_url")
    st.sidebar.checkbox("Auto refresh", key="auto_refresh")
    st.sidebar.slider("Refresh interval (seconds)", 1, 10, key="refresh_seconds")

    api = st.session_state.api_url

    if st.session_state.auto_refresh and hasattr(st, "fragment"):

        @st.fragment(run_every=f"{st.session_state.refresh_seconds}s")
        def live_fragment() -> None:
            render_live_status(api)

        live_fragment()
    else:
        if st.button("Refresh now"):
            st.rerun()
        render_live_status(api)

    st.subheader("Operator Controls")
    mode = st.selectbox("Mode", ["STOPPED", "OBSERVE", "PAPER", "HALTED"], index=0)
    c1, c2, c3 = st.columns(3)
    if c1.button("Apply Mode"):
        notify_action(call_api(api, "POST", "/state/mode", {"mode": mode}), f"Mode set to {mode}.")
    if c2.button("Halt"):
        notify_action(call_api(api, "POST", "/state/halt"), "Bot HALTED.")
    kill_confirm = c3.checkbox("Confirm kill switch")
    if c3.button("Kill Switch", disabled=not kill_confirm):
        notify_action(call_api(api, "POST", "/state/kill"), "Kill switch engaged.")

    st.subheader("Replay")
    replay_path = st.text_input("Replay CSV path", value="sample_data/demo_intraday.csv")
    r1, r2, r3 = st.columns(3)
    if r1.button("Load Replay"):
        notify_action(call_api(api, "POST", "/replay/load", {"path": replay_path}), "Replay dataset loaded.")
    if r2.button("Start Replay"):
        notify_action(call_api(api, "POST", "/replay/start"), "Replay started.")
    if r3.button("Stop Replay"):
        notify_action(call_api(api, "POST", "/replay/stop"), "Replay stop requested.")

    st.subheader("Strategies")
    strategies = call_api(api, "GET", "/strategies")
    if strategies.ok:
        for item in strategies.payload or []:
            c1, c2, c3 = st.columns([2, 1, 1])
            c1.write(item)
            sid = item["strategy_id"]
            if c2.button(f"Enable {sid}"):
                notify_action(call_api(api, "POST", f"/strategies/{sid}/enable"), f"{sid} enabled.")
            if c3.button(f"Disable {sid}"):
                notify_action(call_api(api, "POST", f"/strategies/{sid}/disable"), f"{sid} disabled.")
    else:
        st.error(strategies.error)

    st.subheader("Positions / Orders / Fills")
    st.dataframe(call_api(api, "GET", "/positions").payload or [])
    st.dataframe(call_api(api, "GET", "/orders").payload or [])
    st.dataframe(call_api(api, "GET", "/fills").payload or [])

    st.subheader("Replay Review & Analytics")
    summary = call_api(api, "GET", "/analytics/summary")
    fees = call_api(api, "GET", "/analytics/fees")
    sessions = call_api(api, "GET", "/analytics/sessions")
    trades = call_api(api, "GET", "/analytics/trades")
    decisions = call_api(api, "GET", "/analytics/decisions")

    if summary.ok and isinstance(summary.payload, dict):
        s = summary.payload
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Closed Trades", s.get("total_closed_trades", 0))
        m2.metric("Win Rate", f"{100 * float(s.get('win_rate', 0.0)):.1f}%")
        m3.metric("Net PnL", round(float(s.get("net_realized_pnl", 0.0)), 2))
        m4.metric("Gross PnL", round(float(s.get("gross_realized_pnl", 0.0)), 2))
    else:
        st.warning(summary.error or "Summary unavailable")

    st.write("Fees")
    st.json(fees.payload if fees.ok else {"error": fees.error})

    st.write("Replay Sessions")
    st.dataframe(sessions.payload or [])

    st.write("Closed Trades Journal")
    st.dataframe(trades.payload or [])

    st.write("Decision Audit Trail")
    st.dataframe(decisions.payload or [])

    st.caption("Reset clears simulated analytics records only. Config/runtime metadata remain.")
    if st.checkbox("Confirm analytics reset"):
        if st.button("Reset Analytics Records"):
            notify_action(call_api(api, "POST", "/analytics/reset", {"confirm": True}), "Analytics records cleared.")

    st.subheader("Config / Risk")
    cfg = call_api(api, "GET", "/config")
    st.json((cfg.payload or {}).get("risk", {}))

    st.subheader("Logs")
    st.dataframe(call_api(api, "GET", "/logs").payload or [])


render()
