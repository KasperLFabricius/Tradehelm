"""NYSE trading calendar wrapper.

Thin, version-stable facade over `exchange_calendars` (default XNYS). Sessions
are returned as tz-naive DatetimeIndex/Timestamps normalised to midnight, matching
the bar-frame index (see schema.py).
"""

from __future__ import annotations

import exchange_calendars as xcals
import pandas as pd

from .schema import DataError

# Window used to find the adjacent session; comfortably longer than any exchange
# holiday gap.
_ADJ_WINDOW = pd.Timedelta(days=15)


def _to_naive_dates(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(index)
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    return idx.normalize()


class TradingCalendar:
    """Sessions and session arithmetic for a single exchange (default NYSE)."""

    def __init__(self, name: str = "XNYS") -> None:
        self.name = name
        self._cal = xcals.get_calendar(name)

    def sessions(self, start, end) -> pd.DatetimeIndex:
        """All trading sessions in [start, end] inclusive, as tz-naive dates."""
        s = pd.Timestamp(start).normalize()
        e = pd.Timestamp(end).normalize()
        if e < s:
            raise DataError(f"sessions(): end {e.date()} precedes start {s.date()}")
        return _to_naive_dates(self._cal.sessions_in_range(s, e))

    def is_session(self, day) -> bool:
        ts = pd.Timestamp(day).normalize()
        return bool(self._cal.is_session(ts))

    def previous_session(self, day) -> pd.Timestamp:
        """The last session strictly before `day`."""
        ts = pd.Timestamp(day).normalize()
        window = self.sessions(ts - _ADJ_WINDOW, ts)
        prior = window[window < ts]
        if len(prior) == 0:
            raise DataError(f"No session within {_ADJ_WINDOW.days}d before {ts.date()}")
        return prior[-1]

    def next_session(self, day) -> pd.Timestamp:
        """The first session strictly after `day`."""
        ts = pd.Timestamp(day).normalize()
        window = self.sessions(ts, ts + _ADJ_WINDOW)
        later = window[window > ts]
        if len(later) == 0:
            raise DataError(f"No session within {_ADJ_WINDOW.days}d after {ts.date()}")
        return later[0]

    def missing_sessions(self, index, start, end) -> pd.DatetimeIndex:
        """Sessions expected in [start, end] that are absent from `index`."""
        expected = self.sessions(start, end)
        have = _to_naive_dates(pd.DatetimeIndex(index))
        return expected.difference(have)
