"""Tests for the NYSE trading calendar (tradehelm.data.calendar).

Deterministic and offline: exchange_calendars ships the rules, no network.
"""

import pandas as pd
import pytest

from tradehelm.data import DataError, TradingCalendar

CAL = TradingCalendar()


def test_is_session_known_days():
    assert CAL.is_session("2021-01-04") is True  # first 2021 trading day
    assert CAL.is_session("2021-01-01") is False  # New Year's Day
    assert CAL.is_session("2021-01-18") is False  # MLK Day
    assert CAL.is_session("2021-01-16") is False  # Saturday


def test_sessions_in_january_2021():
    sessions = CAL.sessions("2021-01-01", "2021-01-31")
    assert len(sessions) == 19
    assert sessions[0] == pd.Timestamp("2021-01-04")
    assert sessions.tz is None
    assert pd.Timestamp("2021-01-18") not in sessions  # MLK excluded


def test_previous_and_next_session_across_holiday():
    # 2021-01-04 is preceded by 2020-12-31 (Jan 1 holiday + weekend between).
    assert CAL.previous_session("2021-01-04") == pd.Timestamp("2020-12-31")
    assert CAL.next_session("2020-12-31") == pd.Timestamp("2021-01-04")


def test_missing_sessions_detects_a_gap():
    full = CAL.sessions("2021-01-01", "2021-01-31")
    with_gap = full.delete(5)  # drop one session
    missing = CAL.missing_sessions(with_gap, "2021-01-01", "2021-01-31")
    assert list(missing) == [full[5]]


def test_reversed_range_raises():
    with pytest.raises(DataError):
        CAL.sessions("2021-02-01", "2021-01-01")
