"""Tests for point-in-time S&P 500 membership (tradehelm.data.universe)."""

import pandas as pd
import pytest

from tradehelm.data import DataError, Universe


def test_default_dataset_loads():
    u = Universe.default()
    symbols = u.all_symbols()
    assert "AAPL" in symbols
    # Union over all history is well above a single-day count.
    assert len(symbols) > 800


def test_point_in_time_membership():
    u = Universe.default()
    members_2021 = u.members("2021-06-01")
    assert "AAPL" in members_2021
    assert "TSLA" in members_2021  # joined 2020-12-21
    assert 495 <= len(members_2021) <= 515  # ~500 constituents (+ dual classes)

    members_2019 = u.members("2019-01-01")
    assert "TSLA" not in members_2019  # not yet a member


def test_reentry_intervals_and_gaps():
    df = pd.DataFrame(
        {
            "ticker": ["X", "X"],
            "start_date": ["2010-01-01", "2015-01-01"],
            "end_date": ["2011-01-01", ""],  # first interval closed, second open
        }
    )
    u = Universe(df)
    assert u.members("2010-06-01") == ["X"]  # inside first interval
    assert u.members("2013-01-01") == []  # in the gap between intervals
    assert u.members("2016-01-01") == ["X"]  # inside second (open) interval
    assert u.all_symbols() == ["X"]


def test_missing_columns_raise():
    with pytest.raises(DataError):
        Universe(pd.DataFrame({"ticker": ["X"]}))


def test_malformed_end_date_raises_but_blank_is_open():
    # A non-blank, unparseable end_date must fail loud (not become an open interval).
    bad = pd.DataFrame({"ticker": ["X"], "start_date": ["2010-01-01"], "end_date": ["not-a-date"]})
    with pytest.raises(DataError):
        Universe(bad)
    # A blank end_date remains a legitimately open (still-active) interval.
    ok = pd.DataFrame({"ticker": ["X"], "start_date": ["2010-01-01"], "end_date": [""]})
    assert Universe(ok).members("2024-01-01") == ["X"]


def test_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        Universe.from_csv(tmp_path / "nope.csv")
