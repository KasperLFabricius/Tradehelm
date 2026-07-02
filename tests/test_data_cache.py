"""Tests for the Parquet bar cache (tradehelm.data.cache)."""

import pandas as pd
import pytest

from tradehelm.data import BAR_COLUMNS, DataGapError, ParquetCache, TradingCalendar


def _frame(dates):
    idx = pd.to_datetime(list(dates))
    n = len(idx)
    return pd.DataFrame(
        {
            "open": [1.0] * n,
            "high": [2.0] * n,
            "low": [0.5] * n,
            "close": list(range(n)),
            "adj_close": list(range(n)),
            "volume": [100] * n,
        },
        index=idx,
    )


def test_write_read_roundtrip(tmp_path):
    cache = ParquetCache(tmp_path)
    cache.write("AAPL", _frame(["2020-01-02", "2020-01-03"]))
    back = cache.read("AAPL")
    assert back is not None
    assert list(back.columns) == list(BAR_COLUMNS)
    assert len(back) == 2


def test_read_missing_returns_none(tmp_path):
    assert ParquetCache(tmp_path).read("NOPE") is None
    assert ParquetCache(tmp_path).has("NOPE") is False


def test_update_merges_incrementally(tmp_path):
    cache = ParquetCache(tmp_path)
    cache.update("X", _frame(["2020-01-02", "2020-01-03"]))
    merged = cache.update("X", _frame(["2020-01-03", "2020-01-06"]))  # overlap 01-03
    dates = [d.strftime("%Y-%m-%d") for d in merged.index]
    assert dates == ["2020-01-02", "2020-01-03", "2020-01-06"]


def test_update_newest_row_wins(tmp_path):
    cache = ParquetCache(tmp_path)
    cache.update("X", _frame(["2020-01-02"]))  # close 0
    second = _frame(["2020-01-02"])
    second["close"] = 42.0
    merged = cache.update("X", second)
    assert merged.loc[pd.Timestamp("2020-01-02"), "close"] == 42.0


def test_get_or_fetch_fetches_once_then_serves_from_cache(tmp_path):
    calls = {"n": 0}

    class Src:
        def daily_bars(self, symbol, start, end):
            calls["n"] += 1
            return _frame(["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"])

    cache = ParquetCache(tmp_path)
    cache.get_or_fetch("X", "2020-01-02", "2020-01-07", Src())
    assert calls["n"] == 1
    # Fully-covered sub-range: no second fetch.
    out = cache.get_or_fetch("X", "2020-01-03", "2020-01-06", Src())
    assert calls["n"] == 1
    assert len(out) == 2


def test_path_for_is_filesystem_safe(tmp_path):
    cache = ParquetCache(tmp_path)
    assert cache.path_for("BRK.B").name == "BRK_B.parquet"


def _bars_on(index):
    n = len(index)
    return pd.DataFrame(
        {
            "open": [1.0] * n,
            "high": [1.0] * n,
            "low": [1.0] * n,
            "close": [1.0] * n,
            "adj_close": [1.0] * n,
            "volume": [1] * n,
        },
        index=index,
    )


def test_calendar_coverage_refetches_on_interior_gap(tmp_path):
    cal = TradingCalendar()
    sessions = cal.sessions("2021-01-04", "2021-01-15")  # 10 sessions
    cache = ParquetCache(tmp_path, calendar=cal)
    cache.write("X", _bars_on(sessions.delete(3)))  # drop an interior session

    calls = {"n": 0}

    class Src:
        def daily_bars(self, symbol, start, end):
            calls["n"] += 1
            return _bars_on(sessions)  # complete range

    out = cache.get_or_fetch("X", "2021-01-04", "2021-01-15", Src())
    assert calls["n"] == 1  # interior gap forced a refetch
    assert len(out) == len(sessions)


def test_endpoint_only_coverage_without_calendar(tmp_path):
    cal = TradingCalendar()
    sessions = cal.sessions("2021-01-04", "2021-01-15")
    cache = ParquetCache(tmp_path)  # no calendar
    cache.write("X", _bars_on(sessions.delete(3)))  # interior gap, endpoints intact

    calls = {"n": 0}

    class Src:
        def daily_bars(self, symbol, start, end):
            calls["n"] += 1
            return _bars_on(sessions)

    cache.get_or_fetch("X", "2021-01-04", "2021-01-15", Src())
    assert calls["n"] == 0  # endpoint-only check is satisfied (documented weaker guarantee)


def test_get_or_fetch_raises_on_persistent_interior_gap(tmp_path):
    cal = TradingCalendar()
    sessions = cal.sessions("2021-01-04", "2021-01-15")

    class GappySrc:
        def daily_bars(self, symbol, start, end):
            return _bars_on(sessions.delete(3))  # source itself returns an interior gap

    cache = ParquetCache(tmp_path, calendar=cal)
    with pytest.raises(DataGapError):
        cache.get_or_fetch("X", "2021-01-04", "2021-01-15", GappySrc())


def test_get_or_fetch_allows_tail_truncation(tmp_path):
    # A delisted / short-history symbol legitimately ends before the requested
    # end: contiguous data, no interior gap -> returned, not an error.
    cal = TradingCalendar()
    full = cal.sessions("2021-01-04", "2021-01-29")
    available = full[:8]

    class Src:
        def daily_bars(self, symbol, start, end):
            return _bars_on(available)

    cache = ParquetCache(tmp_path, calendar=cal)
    out = cache.get_or_fetch("X", "2021-01-04", "2021-01-29", Src())
    assert len(out) == 8
