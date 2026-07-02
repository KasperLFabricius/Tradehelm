"""Tests for data-quality logic (tradehelm.data.quality); synthetic frames only."""

import pandas as pd

from tradehelm.data import TradingCalendar
from tradehelm.data.quality import build_report, missing_sessions_for, split_check


def _frame(dates, close, adj):
    idx = pd.to_datetime(list(dates))
    return pd.DataFrame(
        {
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "adj_close": adj,
            "volume": [1] * len(idx),
        },
        index=idx,
    )


def test_split_check_pass_when_adjusted_is_continuous():
    # Raw close drops ~4x at the split; adjusted close stays continuous.
    df = _frame(
        ["2020-08-27", "2020-08-28", "2020-08-31", "2020-09-01"],
        close=[500.0, 504.0, 126.0, 127.0],
        adj=[125.0, 126.0, 126.0, 127.0],
    )
    result = split_check(df, "2020-08-31", 4.0)
    assert result["status"] == "PASS"
    assert 3.5 < result["raw_ratio"] < 4.5  # 504 / 126
    assert 0.9 < result["adj_ratio"] < 1.1  # continuous


def test_split_check_warns_when_adjusted_jumps():
    df = _frame(["2020-08-28", "2020-08-31"], close=[504.0, 126.0], adj=[504.0, 126.0])
    result = split_check(df, "2020-08-31", 4.0)
    assert result["status"] == "WARN"
    assert result["adj_ratio"] > 3


def test_split_check_reports_no_data_when_nothing_before():
    df = _frame(["2020-08-31", "2020-09-01"], close=[126.0, 127.0], adj=[126.0, 127.0])
    result = split_check(df, "2020-08-31", 4.0)
    assert result["status"] == "NO_DATA"


def test_missing_sessions_detects_a_dropped_day():
    cal = TradingCalendar()
    sessions = cal.sessions("2021-01-04", "2021-01-15")
    kept = sessions.delete(2)  # remove an interior session
    df = pd.DataFrame(
        {"open": 1, "high": 1, "low": 1, "close": 1, "adj_close": 1, "volume": 1},
        index=kept,
    )
    missing = missing_sessions_for(df, cal)
    assert list(missing) == [sessions[2]]


def test_build_report_renders_tables_and_missing_symbols():
    coverage = [
        {"symbol": "AAPL", "bars": 100, "missing": 0, "first": "2020-01-02", "last": "2020-06-01"}
    ]
    splits = [
        {"name": "Apple 4:1", "status": "PASS", "raw_ratio": 4.0, "adj_ratio": 1.0, "expected": 4.0}
    ]
    md = build_report(coverage, splits, missing_symbols=["AABA", "AAMRQ"])
    assert "Data-quality report" in md
    assert "AAPL" in md
    assert "Apple 4:1" in md
    # Cache-miss constituents are surfaced, not silently dropped.
    assert "no cached data (2)" in md
    assert "AABA" in md and "AAMRQ" in md
