"""Tests for the canonical bar schema (tradehelm.data.schema)."""

import pandas as pd
import pytest

from tradehelm.data import BAR_COLUMNS, DataError, EmptyDataError, ensure_bar_frame


def _yf_frame(dates=("2020-01-03", "2020-01-02")):
    # yfinance-style: capitalised columns, "Adj Close", possibly unsorted index.
    idx = pd.to_datetime(list(dates))
    n = len(idx)
    return pd.DataFrame(
        {
            "Open": [2.0, 1.0][:n],
            "High": [3.0, 2.0][:n],
            "Low": [1.0, 0.5][:n],
            "Close": [2.5, 1.5][:n],
            "Adj Close": [2.4, 1.4][:n],
            "Volume": [100, 200][:n],
        },
        index=idx,
    )


def test_normalizes_columns_sorts_and_drops_tz():
    out = ensure_bar_frame(_yf_frame(), symbol="X")
    assert list(out.columns) == list(BAR_COLUMNS)
    assert out.index.is_monotonic_increasing
    assert out.index.tz is None
    assert out.index.name == "date"


def test_empty_frame_raises():
    with pytest.raises(EmptyDataError):
        ensure_bar_frame(pd.DataFrame(), symbol="X")
    with pytest.raises(EmptyDataError):
        ensure_bar_frame(None, symbol="X")


def test_missing_column_raises():
    with pytest.raises(DataError):
        ensure_bar_frame(_yf_frame().drop(columns=["Volume"]), symbol="X")


def test_tz_aware_index_is_normalized_to_naive_dates():
    idx = pd.to_datetime(["2020-01-02", "2020-01-03"]).tz_localize("America/New_York")
    df = pd.DataFrame(
        {
            "open": [1.0, 2.0],
            "high": [2.0, 3.0],
            "low": [0.5, 1.0],
            "close": [1.5, 2.5],
            "adj_close": [1.4, 2.4],
            "volume": [100, 200],
        },
        index=idx,
    )
    out = ensure_bar_frame(df, symbol="X")
    assert out.index.tz is None
    assert list(out.index) == [pd.Timestamp("2020-01-02"), pd.Timestamp("2020-01-03")]


def test_partial_row_with_any_missing_field_is_dropped():
    idx = pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-06"])
    df = pd.DataFrame(
        {
            "open": [1.0, 2.0, 3.0],
            "high": [1.0, 2.0, 3.0],
            "low": [1.0, 2.0, 3.0],
            "close": [1.0, float("nan"), 3.0],  # row 2: NaN close
            "adj_close": [1.0, 2.0, float("nan")],  # row 3: NaN adj_close
            "volume": [100, 200, 300],
        },
        index=idx,
    )
    out = ensure_bar_frame(df, symbol="X")
    assert list(out.index) == [pd.Timestamp("2020-01-02")]  # only the complete row survives


def test_duplicate_dates_keep_last():
    idx = pd.to_datetime(["2020-01-02", "2020-01-02"])
    df = pd.DataFrame(
        {
            "open": [1.0, 9.0],
            "high": [1.0, 9.0],
            "low": [1.0, 9.0],
            "close": [1.0, 9.0],
            "adj_close": [1.0, 9.0],
            "volume": [1, 9],
        },
        index=idx,
    )
    out = ensure_bar_frame(df, symbol="X")
    assert len(out) == 1
    assert out.iloc[0]["close"] == 9.0
