"""Tests for the Parquet bar cache (tradehelm.data.cache)."""

import pandas as pd
import pytest

from tradehelm.data import (
    BAR_COLUMNS,
    DataGapError,
    EmptyDataError,
    ParquetCache,
    TradingCalendar,
)


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
    # Gap is validated BEFORE writing, so nothing poisons the cache.
    assert cache.read("X") is None


def test_extension_refetches_full_span_for_consistent_adj_close(tmp_path):
    # Extending after a corporate action re-fetches the full span so historical
    # adj_close is refreshed - no stale pre-event adjusted prices left behind.
    cal = TradingCalendar()
    days = cal.sessions("2021-01-04", "2021-01-15")
    cache = ParquetCache(tmp_path, calendar=cal)
    first = _bars_on(days[:6])
    first["adj_close"] = 1.0
    cache.write("X", first)  # cached with old adjusted prices

    seen = {}

    class Src:
        def daily_bars(self, symbol, start, end):
            seen["start"] = pd.Timestamp(start)
            fresh = _bars_on(days)  # full span, retroactively re-adjusted
            fresh["adj_close"] = 2.0
            return fresh

    out = cache.get_or_fetch("X", "2021-01-04", "2021-01-15", Src())
    assert seen["start"] == days[0]  # full span re-fetched, not just the new tail
    assert (out["adj_close"] == 2.0).all()  # no stale 1.0 from the cached rows


def test_partial_interior_refill_still_raises(tmp_path):
    # Cache has bars on both sides of a hole; a refill that returns only part of
    # the missing interior window must fail loud (the hole persists).
    cal = TradingCalendar()
    days = cal.sessions("2021-01-04", "2021-01-08")  # 5 sessions
    cache = ParquetCache(tmp_path, calendar=cal)
    cache.write("X", _bars_on(days[[0, 4]]))  # only first + last; 3 interior missing

    class PartialSrc:
        def daily_bars(self, symbol, start, end):
            return _bars_on(days[[1]])  # returns only one of the three missing days

    with pytest.raises(DataGapError):
        cache.get_or_fetch("X", "2021-01-04", "2021-01-08", PartialSrc())


def test_empty_interior_refill_raises(tmp_path):
    # An empty refill of an INTERIOR hole must fail loud (unlike a delisted tail).
    cal = TradingCalendar()
    days = cal.sessions("2021-01-04", "2021-01-08")
    cache = ParquetCache(tmp_path, calendar=cal)
    cache.write("X", _bars_on(days[[0, 4]]))

    class EmptySrc:
        def daily_bars(self, symbol, start, end):
            raise EmptyDataError("no data")

    with pytest.raises(DataGapError):
        cache.get_or_fetch("X", "2021-01-04", "2021-01-08", EmptySrc())


def test_request_starting_inside_a_hole_raises(tmp_path):
    # A cache with a disjoint Jan + Mar (no Feb); a request for Feb->early-Mar whose
    # fetch returns no Feb must fail loud even though the slice's available rows
    # (March) are contiguous. The Feb hole is interior to the Jan..Mar cached span.
    cal = TradingCalendar()
    jan = cal.sessions("2021-01-04", "2021-01-29")
    mar = cal.sessions("2021-03-01", "2021-03-31")
    cache = ParquetCache(tmp_path, calendar=cal)
    cache.write("X", _bars_on(jan.append(mar)))  # disjoint: Jan and Mar, no Feb

    class EmptyFebSrc:
        def daily_bars(self, symbol, start, end):
            raise EmptyDataError("no February data")

    with pytest.raises(DataGapError):
        cache.get_or_fetch("X", "2021-02-15", "2021-03-05", EmptyFebSrc())


def test_extension_tolerates_empty_fetch_for_cached_symbol(tmp_path):
    # An empty fetch when extending an already-cached symbol (transient empty /
    # dead tail) returns the cached history rather than raising.
    cal = TradingCalendar()
    days = cal.sessions("2021-01-04", "2021-01-13")
    cache = ParquetCache(tmp_path, calendar=cal)
    cache.write("X", _bars_on(days))

    class EmptySrc:
        def daily_bars(self, symbol, start, end):
            raise EmptyDataError("no more bars")

    out = cache.get_or_fetch("X", "2021-01-04", "2021-01-20", EmptySrc())
    assert len(out) == len(days)


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


def test_covers_snaps_non_session_bounds_to_sessions(tmp_path):
    # Request starts on a Saturday (2021-01-02); a cache beginning on the first
    # actual session must count as covered, not trigger a redownload.
    cal = TradingCalendar()
    sessions = cal.sessions("2021-01-04", "2021-01-15")
    cache = ParquetCache(tmp_path, calendar=cal)
    cache.write("X", _bars_on(sessions))

    calls = {"n": 0}

    class Src:
        def daily_bars(self, symbol, start, end):
            calls["n"] += 1
            return _bars_on(sessions)

    cache.get_or_fetch("X", "2021-01-02", "2021-01-16", Src())  # Sat..Sat bounds
    assert calls["n"] == 0
