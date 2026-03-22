"""Streamlit operator dashboard for TradeHelm."""
from __future__ import annotations

import requests
import streamlit as st

API = st.sidebar.text_input("Control API URL", value="http://127.0.0.1:8000")

st.title("TradeHelm Dashboard")


def get(path: str):
    return requests.get(f"{API}{path}", timeout=10).json()


def post(path: str, payload: dict | None = None):
    return requests.post(f"{API}{path}", json=payload or {}, timeout=30).json()


state = get("/state")
st.header("Command Center")
st.metric("Mode", state.get("mode", "UNKNOWN"))
st.metric("Replay Running", str(state.get("replay_running", False)))
st.metric("Replay Stop Requested", str(state.get("replay_stop_requested", False)))
st.caption(f"Replay Path: {state.get('replay_path')}")
st.caption(f"Started: {state.get('replay_started_at')}")
st.caption(f"Completed: {state.get('replay_completed_at')}")
st.json(state)

mode = st.selectbox("Bot Mode", ["STOPPED", "OBSERVE", "PAPER", "HALTED", "KILL_SWITCH"])
if st.button("Switch mode"):
    st.write(post("/state/mode", {"mode": mode}))

col1, col2, col3 = st.columns(3)
if col1.button("Halt"):
    st.write(post("/state/halt"))
if col2.button("Kill Switch"):
    st.write(post("/state/kill"))
if col3.button("Reset STOPPED"):
    st.write(post("/state/mode", {"mode": "STOPPED"}))

st.subheader("Replay")
replay_path = st.text_input("Replay CSV path", value="sample_data/demo_intraday.csv")
if st.button("Load replay"):
    st.write(post("/replay/load", {"path": replay_path}))
if st.button("Start replay"):
    st.write(post("/replay/start"))
if st.button("Stop replay"):
    st.write(post("/replay/stop"))

st.header("Strategies")
for item in get("/strategies"):
    c1, c2, c3 = st.columns([2, 1, 1])
    c1.write(item)
    if c2.button(f"Enable {item['strategy_id']}"):
        st.write(post(f"/strategies/{item['strategy_id']}/enable"))
    if c3.button(f"Disable {item['strategy_id']}"):
        st.write(post(f"/strategies/{item['strategy_id']}/disable"))

st.header("Orders and Fills")
st.dataframe(get("/orders"))
st.dataframe(get("/fills"))

st.header("Positions")
st.dataframe(get("/positions"))

st.header("Risk")
st.json(get("/config").get("risk", {}))

st.header("Logs")
st.dataframe(get("/logs"))
