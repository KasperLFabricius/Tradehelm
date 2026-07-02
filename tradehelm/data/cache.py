"""Local Parquet cache for daily bars.

One Parquet file per symbol under the configured cache directory. Writes and
reads normalise through ensure_bar_frame; update() merges incrementally (union of
dates, newest row wins) so pull_data can extend history cheaply.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .calendar import TradingCalendar
from .schema import DataGapError, EmptyDataError, ensure_bar_frame
from .sources import BarSource


def _safe_name(symbol: str) -> str:
    # Deterministic, filesystem-safe; only ever used one-way (write and read
    # both derive it from the symbol), so lossiness is fine.
    return symbol.upper().replace("/", "-").replace("\\", "-").replace(".", "_")


class ParquetCache:
    def __init__(self, cache_dir: str | Path, calendar: TradingCalendar | None = None) -> None:
        self.cache_dir = Path(cache_dir)
        # When set, coverage requires every expected trading session to be present,
        # not just matching endpoints - so an interior gap triggers a refetch.
        self._calendar = calendar

    def path_for(self, symbol: str) -> Path:
        return self.cache_dir / f"{_safe_name(symbol)}.parquet"

    def has(self, symbol: str) -> bool:
        return self.path_for(symbol).exists()

    def read(self, symbol: str) -> pd.DataFrame | None:
        path = self.path_for(symbol)
        if not path.exists():
            return None
        return ensure_bar_frame(pd.read_parquet(path), symbol=symbol)

    def write(self, symbol: str, df: pd.DataFrame) -> pd.DataFrame:
        frame = ensure_bar_frame(df, symbol=symbol)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        frame.to_parquet(self.path_for(symbol))
        return frame

    def update(self, symbol: str, df: pd.DataFrame) -> pd.DataFrame:
        """Merge new bars into any existing cached history (newest row wins)."""
        new = ensure_bar_frame(df, symbol=symbol)
        existing = self.read(symbol)
        if existing is not None:
            combined = pd.concat([existing, new])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        else:
            combined = new
        return self.write(symbol, combined)

    def _covers(self, df: pd.DataFrame, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> bool:
        if len(df) == 0:
            return False
        if self._calendar is not None:
            # Compare against expected SESSIONS, not raw dates, so a request
            # starting on a non-session (e.g. 2005-01-01, a Saturday) is covered
            # by a cache that begins on the first actual session; also catches
            # interior gaps. A truncated history (delisted / IPO) is NOT covered
            # here, so its missing tail is (cheaply) re-probed - see get_or_fetch.
            expected = self._calendar.sessions(start_ts, end_ts)
            if len(expected) == 0:
                return True
            return len(self._calendar.missing_sessions(df.index, expected[0], expected[-1])) == 0
        return df.index.min() <= start_ts and df.index.max() >= end_ts

    def _plan_fetch(
        self, cached: pd.DataFrame | None, start_ts: pd.Timestamp, end_ts: pd.Timestamp
    ) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
        """Windows to fetch. With a calendar, only the missing sessions (so a
        daily run extending yesterday's cache fetches just today, not the whole
        history). Without one, refetch the full range (safe fallback)."""
        if cached is None or len(cached) == 0 or self._calendar is None:
            return [(start_ts, end_ts)]
        missing = self._calendar.sessions(start_ts, end_ts).difference(cached.index)
        if len(missing) == 0:
            return [(start_ts, end_ts)]  # safety; normally already "covered"
        return [(missing.min(), missing.max())]

    def _requested_interior_gaps(
        self, merged: pd.DataFrame, start_ts: pd.Timestamp, end_ts: pd.Timestamp
    ) -> pd.DatetimeIndex:
        """Sessions in [start, end] that are missing AND interior to the symbol's
        OVERALL cached span (data on both sides).

        Classifying against the whole merged span - not the sliced request - means
        a hole at the edge of the requested window is still caught (e.g. Feb missing
        between cached Jan and Mar when the request starts in Feb). Missing sessions
        before the first or after the last cached bar are legitimate IPO / delisting
        / not-yet-published truncation, not gaps.
        """
        if self._calendar is None or len(merged) == 0:
            return pd.DatetimeIndex([])
        missing = self._calendar.sessions(start_ts, end_ts).difference(merged.index)
        lo, hi = merged.index.min(), merged.index.max()
        return missing[(missing > lo) & (missing < hi)]

    def get_or_fetch(
        self,
        symbol: str,
        start,
        end,
        source: BarSource,
        *,
        refresh: bool = False,
    ) -> pd.DataFrame:
        """Return bars for [start, end], fetching + caching only if not covered.

        Coverage is session-based (calendar cache): the cache must contain every
        trading session in the range. When a fetch is needed, only the missing
        sessions are pulled and merged - so a live symbol fetches just its new tail,
        and a delisted one does a single cheap empty probe of its dead tail (which
        is tolerated). Coverage is never inferred from a stored marker, so a
        request can never be served from partial/empty data.

        The merged frame is validated for INTERIOR session gaps and raises
        DataGapError BEFORE writing (a failed check never poisons the cache).
        Head/tail truncation - IPO, delisting, today's unpublished bar - is NOT a
        gap; an empty extension of an already-cached symbol is tolerated.
        """
        start_ts = pd.Timestamp(start).normalize()
        end_ts = pd.Timestamp(end).normalize()
        cached = None if refresh else self.read(symbol)
        if cached is not None and self._covers(cached, start_ts, end_ts):
            return cached.loc[start_ts:end_ts]

        frames = [] if cached is None else [cached]
        for fetch_start, fetch_end in self._plan_fetch(cached, start_ts, end_ts):
            try:
                frames.append(
                    ensure_bar_frame(
                        source.daily_bars(symbol, fetch_start, fetch_end), symbol=symbol
                    )
                )
            except EmptyDataError:
                # Tolerated only for an already-cached symbol (a delisted dead tail);
                # a first fetch with no data is a genuine failure. The requested-range
                # gap check below still fails loud if this leaves an interior hole.
                if cached is None:
                    raise

        combined = pd.concat(frames)
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        # Validate the requested range for interior gaps BEFORE writing. Catches a
        # partial/empty refill of an interior hole (including one at the edge of the
        # request), while allowing head/tail truncation and gaps between separately
        # requested ranges.
        gaps = self._requested_interior_gaps(combined, start_ts, end_ts)
        if len(gaps):
            raise DataGapError(
                f"{symbol}: {len(gaps)} interior session gap(s) in "
                f"[{start_ts.date()}, {end_ts.date()}] (e.g. {gaps[0].date()}); "
                "refusing to cache holey data"
            )
        merged = self.write(symbol, combined)
        return merged.loc[start_ts:end_ts]
