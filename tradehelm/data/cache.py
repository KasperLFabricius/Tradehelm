"""Local Parquet cache for daily bars.

One Parquet file per symbol under the configured cache directory. Writes and
reads normalise through ensure_bar_frame; update() merges incrementally (union of
dates, newest row wins) so pull_data can extend history cheaply.
"""

from __future__ import annotations

import json
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

    def _meta_path(self, symbol: str) -> Path:
        return self.cache_dir / f"{_safe_name(symbol)}.meta.json"

    def _read_window(self, symbol: str) -> tuple[pd.Timestamp, pd.Timestamp] | None:
        """The [start, end] window previously fetched from the source for this
        symbol (what we KNOW we've asked for), or None."""
        path = self._meta_path(symbol)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return pd.Timestamp(data["fetched_start"]), pd.Timestamp(data["fetched_end"])
        except (ValueError, KeyError, OSError):
            return None

    def _record_window(self, symbol: str, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> None:
        prev = self._read_window(symbol)
        lo = min(start_ts, prev[0]) if prev else start_ts
        hi = max(end_ts, prev[1]) if prev else end_ts
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._meta_path(symbol).write_text(
            json.dumps(
                {"fetched_start": lo.date().isoformat(), "fetched_end": hi.date().isoformat()}
            ),
            encoding="utf-8",
        )

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

    def _covers(
        self, symbol: str, df: pd.DataFrame, start_ts: pd.Timestamp, end_ts: pd.Timestamp
    ) -> bool:
        if len(df) == 0:
            return False
        # If we've already fetched this whole window, we're covered even when the
        # symbol's real history is shorter (IPO after start / delisted before end):
        # on-disk data is guaranteed interior-gap-free (validated before write).
        window = self._read_window(symbol)
        if window is not None and window[0] <= start_ts and end_ts <= window[1]:
            return True
        if self._calendar is not None:
            # Fallback for caches without a recorded window (manual / update()):
            # compare against expected SESSIONS, not raw dates, so a request
            # starting on a non-session (e.g. 2005-01-01, a Saturday) is covered
            # by a cache that begins on the first actual session.
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

    def _interior_gaps(self, df: pd.DataFrame) -> pd.DatetimeIndex:
        """Missing sessions strictly WITHIN the frame's own span.

        This is the genuine-corruption signal (a partial response or manual edit
        dropped a day). It deliberately ignores head/tail truncation relative to a
        requested range, because a symbol legitimately has no bars before its IPO,
        after a delisting, or for today's not-yet-published session.
        """
        if self._calendar is None or len(df) == 0:
            return pd.DatetimeIndex([])
        return self._calendar.missing_sessions(df.index, df.index.min(), df.index.max())

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

        "Covered" means the requested window is within the range we've already
        fetched for this symbol (recorded per symbol), so a truncated history
        (IPO / delisting) counts as covered and is not re-fetched every run. When
        a fetch is needed, only the missing sessions are pulled and merged.

        The merged frame is validated for INTERIOR session gaps and raises
        DataGapError BEFORE writing (a failed check never poisons the cache).
        Head/tail truncation - IPO, delisting, today's unpublished bar - is NOT a
        gap; an empty extension of an already-cached symbol is tolerated.
        """
        start_ts = pd.Timestamp(start).normalize()
        end_ts = pd.Timestamp(end).normalize()
        cached = None if refresh else self.read(symbol)
        if cached is not None and self._covers(symbol, cached, start_ts, end_ts):
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
                # No new data for this window. For a first fetch that's a genuine
                # failure; for an extension of an already-cached symbol it just
                # means the (e.g. delisted) tail has no more bars - keep what we have.
                if cached is None:
                    raise

        combined = pd.concat(frames)
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        # Validate BEFORE writing so a failed gap check never poisons the cache.
        gaps = self._interior_gaps(combined)
        if len(gaps):
            raise DataGapError(
                f"{symbol}: {len(gaps)} interior session gap(s) after fetch "
                f"(e.g. {gaps[0].date()}); refusing to return holey data"
            )
        merged = self.write(symbol, combined)
        self._record_window(symbol, start_ts, end_ts)
        return merged.loc[start_ts:end_ts]
