"""Streamlit operator dashboard for TradeHelm."""
from __future__ import annotations

import streamlit as st

from tradehelm.dashboard.client import ApiResult, call_api

DEFAULT_API = "http://127.0.0.1:8000"
DEFAULT_INTERVALS = ["1min", "5min", "15min", "30min", "1h"]


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

    intervals_result = call_api(api, "GET", "/historical/intervals")
    intervals_payload = intervals_result.payload if intervals_result.ok and isinstance(intervals_result.payload, dict) else {}
    intervals = intervals_payload.get("intervals", DEFAULT_INTERVALS)
    default_interval = intervals_payload.get("default", "5min")
    default_interval_index = intervals.index(default_interval) if default_interval in intervals else 0

    st.subheader("Historical Backtesting (Twelve Data, US equities, cached)")
    symbols_text = st.text_input("Tickers (comma-separated)", value="AAPL,MSFT")
    h1, h2, h3 = st.columns(3)
    start_date = h1.date_input("Start date")
    end_date = h2.date_input("End date")
    interval = h3.selectbox("Interval", intervals, index=default_interval_index)
    adjusted = st.toggle("Adjusted intraday bars (split-adjusted)", value=True)

    cfg_result = call_api(api, "GET", "/config")
    cfg_payload = cfg_result.payload if cfg_result.ok and isinstance(cfg_result.payload, dict) else {}
    orb_cfg = ((cfg_payload.get("strategies") or {}).get("orb") or {})
    vwap_cfg = ((cfg_payload.get("strategies") or {}).get("vwap") or {})

    with st.expander("Backtest strategy parameters", expanded=False):
        st.write("ORB")
        orb_opening_range_bars = st.number_input("opening_range_bars", min_value=1, value=int(orb_cfg.get("opening_range_bars", 3)), key="orb_opening_range_bars")
        orb_breakout_buffer = st.number_input("breakout_buffer", min_value=0.0, value=float(orb_cfg.get("breakout_buffer", 0.05)), key="orb_breakout_buffer")
        orb_direction = st.selectbox("ORB direction", ["LONG", "SHORT", "BOTH"], index=["LONG", "SHORT", "BOTH"].index(str(orb_cfg.get("direction", "BOTH"))), key="orb_direction")
        orb_stop_loss = st.number_input("stop_loss", min_value=0.0001, value=float(orb_cfg.get("stop_loss", 0.4)), key="orb_stop_loss")
        orb_take_profit = st.number_input("take_profit", min_value=0.0001, value=float(orb_cfg.get("take_profit", 0.8)), key="orb_take_profit")
        orb_max_bars = st.number_input("max_bars_in_trade", min_value=1, value=int(orb_cfg.get("max_bars_in_trade", 12)), key="orb_max_bars")
        orb_flatten = st.checkbox("flatten_end_of_session", value=bool(orb_cfg.get("flatten_end_of_session", True)), key="orb_flatten")

        st.write("VWAP")
        vwap_pullback_threshold = st.number_input("pullback_threshold", min_value=0.0001, value=float(vwap_cfg.get("pullback_threshold", 0.15)), key="vwap_pullback_threshold")
        vwap_reentry_buffer = st.number_input("reentry_buffer", min_value=0.0, value=float(vwap_cfg.get("reentry_buffer", 0.05)), key="vwap_reentry_buffer")
        vwap_direction = st.selectbox("VWAP direction", ["LONG", "SHORT", "BOTH"], index=["LONG", "SHORT", "BOTH"].index(str(vwap_cfg.get("direction", "BOTH"))), key="vwap_direction")
        vwap_stop_loss = st.number_input("VWAP stop_loss", min_value=0.0001, value=float(vwap_cfg.get("stop_loss", 0.35)), key="vwap_stop_loss")
        vwap_take_profit = st.number_input("VWAP take_profit", min_value=0.0001, value=float(vwap_cfg.get("take_profit", 0.7)), key="vwap_take_profit")
        vwap_max_bars = st.number_input("VWAP max_bars_in_trade", min_value=1, value=int(vwap_cfg.get("max_bars_in_trade", 10)), key="vwap_max_bars")

    symbols = [s.strip().upper() for s in symbols_text.split(",") if s.strip()]
    request_payload = {
        "symbols": symbols,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "interval": interval,
        "adjusted": adjusted,
        "use_existing_cache": True,
    }
    strategy_config_patch = {
        "strategies": {
            "orb": {
                "opening_range_bars": int(orb_opening_range_bars),
                "breakout_buffer": float(orb_breakout_buffer),
                "direction": orb_direction,
                "stop_loss": float(orb_stop_loss),
                "take_profit": float(orb_take_profit),
                "max_bars_in_trade": int(orb_max_bars),
                "flatten_end_of_session": bool(orb_flatten),
            },
            "vwap": {
                "pullback_threshold": float(vwap_pullback_threshold),
                "reentry_buffer": float(vwap_reentry_buffer),
                "direction": vwap_direction,
                "stop_loss": float(vwap_stop_loss),
                "take_profit": float(vwap_take_profit),
                "max_bars_in_trade": int(vwap_max_bars),
            },
        }
    }

    hb1, hb2 = st.columns(2)
    if hb1.button("Fetch / Cache Historical Data"):
        notify_action(call_api(api, "POST", "/historical/fetch", request_payload), "Historical data fetched/cached.")
    if hb2.button("Run Cached Backtest"):
        current_cfg_result = call_api(api, "GET", "/config")
        if not current_cfg_result.ok or not isinstance(current_cfg_result.payload, dict):
            st.error(current_cfg_result.error or "Unable to load config")
        else:
            updated_cfg = current_cfg_result.payload
            updated_cfg.update(strategy_config_patch)
            cfg_update = call_api(api, "POST", "/config", {"config": updated_cfg})
            if not cfg_update.ok:
                st.error(cfg_update.error or "Failed to apply strategy params")
            else:
                run_payload = dict(request_payload)
                run_payload.pop("use_existing_cache", None)
                notify_action(call_api(api, "POST", "/backtests/run", run_payload), "Backtest run complete.")

    st.write("Cached Datasets")
    st.dataframe(call_api(api, "GET", "/historical/datasets").payload or [])
    st.write("Backtest Runs")
    runs_result = call_api(api, "GET", "/backtests/runs")
    runs_payload = runs_result.payload or []
    st.dataframe(runs_payload)

    st.subheader("Backtest Run Detail")
    run_options = {f"Run {row['id']} ({row.get('interval')} | {','.join(row.get('symbols', []))})": row["id"] for row in runs_payload if isinstance(row, dict) and "id" in row}
    if run_options:
        selected_run_label = st.selectbox("Select run", list(run_options.keys()), key="run_detail_selector")
        selected_run_id = run_options[selected_run_label]
        run_detail = call_api(api, "GET", f"/backtests/{selected_run_id}")
        if run_detail.ok and isinstance(run_detail.payload, dict):
            rd = run_detail.payload
            st.write("Config snapshot")
            st.json(rd.get("config", {}))
            st.write("Summary metrics")
            st.json(rd.get("summary", {}))
            st.write("Decision summary")
            st.json(rd.get("decision_summary", {}))
            st.write("Equity curve")
            st.line_chart(rd.get("equity_curve", []), x="timestamp", y="equity")
            st.write("Per-symbol breakdown")
            st.dataframe(rd.get("symbol_summary", []))
            st.write("Trade list")
            st.dataframe(rd.get("trades", []))
        else:
            st.warning(run_detail.error or "Run detail unavailable")

    st.subheader("Backtest Comparison")
    if run_options:
        selected_compare_labels = st.multiselect("Select 2+ runs", list(run_options.keys()), key="run_compare_selector")
        if st.button("Compare selected runs") and len(selected_compare_labels) >= 2:
            run_ids = [run_options[label] for label in selected_compare_labels]
            compare_result = call_api(api, "POST", "/backtests/compare", {"run_ids": run_ids})
            if compare_result.ok and isinstance(compare_result.payload, dict):
                compare_payload = compare_result.payload
                st.dataframe(compare_payload.get("runs", []))
                st.write("Per-symbol detail")
                for row in compare_payload.get("runs", []):
                    st.write(f"Run {row.get('run_id')}")
                    st.dataframe(row.get("symbol_summary", []))
            else:
                st.warning(compare_result.error or "Comparison unavailable")

    st.subheader("Strategies")
    strategies = call_api(api, "GET", "/strategies")
    if strategies.ok:
        for item in strategies.payload or []:
            c1, c2, c3 = st.columns([2, 1, 1])
            sid = item["strategy_id"]
            c1.write(f"{sid} enabled={item.get('enabled')}")
            c1.json(item.get("status", {}))
            if c2.button(f"Enable {sid}"):
                notify_action(call_api(api, "POST", f"/strategies/{sid}/enable"), f"{sid} enabled.")
            if c3.button(f"Disable {sid}"):
                notify_action(call_api(api, "POST", f"/strategies/{sid}/disable"), f"{sid} disabled.")
    else:
        st.error(strategies.error)


render()
