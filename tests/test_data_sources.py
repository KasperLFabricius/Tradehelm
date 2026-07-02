"""Tests for YFinanceSource parsing/retry logic (no network; injected downloader)."""

import pandas as pd
import pytest

from tradehelm.data import BAR_COLUMNS, DataError, YFinanceSource


def _yf_raw(n=3):
    idx = pd.bdate_range("2020-01-02", periods=n)
    return pd.DataFrame(
        {
            "Open": range(n),
            "High": range(1, n + 1),
            "Low": range(n),
            "Close": range(n),
            "Adj Close": range(n),
            "Volume": [100] * n,
        },
        index=idx,
        dtype=float,
    )


def test_downloader_output_is_normalized():
    src = YFinanceSource(downloader=lambda s, a, b: _yf_raw())
    out = src.daily_bars("AAPL", "2020-01-01", "2020-01-10")
    assert list(out.columns) == list(BAR_COLUMNS)
    assert out.index.is_monotonic_increasing


def test_multiindex_columns_are_flattened():
    raw = _yf_raw(2)
    raw.columns = pd.MultiIndex.from_product([list(raw.columns), ["AAPL"]])
    src = YFinanceSource(downloader=lambda s, a, b: raw)
    out = src.daily_bars("AAPL", "2020-01-01", "2020-01-10")
    assert "adj_close" in out.columns
    assert len(out) == 2


def test_retries_then_succeeds():
    calls = {"n": 0}

    def flaky(symbol, start, end):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("transient network error")
        return _yf_raw()

    src = YFinanceSource(downloader=flaky, retries=3, backoff=0.0, sleep=lambda _: None)
    out = src.daily_bars("X", "2020-01-01", "2020-01-10")
    assert calls["n"] == 2
    assert len(out) == 3


def test_persistent_empty_raises_dataerror():
    src = YFinanceSource(
        downloader=lambda s, a, b: pd.DataFrame(),
        retries=2,
        backoff=0.0,
        sleep=lambda _: None,
    )
    with pytest.raises(DataError):
        src.daily_bars("X", "2020-01-01", "2020-01-10")


def test_invalid_retries_rejected():
    with pytest.raises(ValueError):
        YFinanceSource(retries=0)


def test_translates_share_class_and_makes_end_exclusive():
    seen = {}

    def recording(symbol, start, end):
        seen["symbol"] = symbol
        seen["start"] = start
        seen["end"] = end
        return _yf_raw()

    src = YFinanceSource(downloader=recording)
    src.daily_bars("BRK.B", "2020-01-02", "2020-01-10")
    # Yahoo share-class symbol, and the inclusive end is advanced by one day
    # because yfinance treats `end` as exclusive.
    assert seen["symbol"] == "BRK-B"
    assert pd.Timestamp(seen["end"]) == pd.Timestamp("2020-01-11")
