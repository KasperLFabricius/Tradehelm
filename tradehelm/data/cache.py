"""Local Parquet cache for daily bars.

One Parquet file per symbol under the configured cache directory. Writes and
reads normalise through ensure_bar_frame; update() merges incrementally (union of
dates, newest row wins) so pull_data can extend history cheaply.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .schema import ensure_bar_frame
from .sources import BarSource


def _safe_name(symbol: str) -> str:
    # Deterministic, filesystem-safe; only ever used one-way (write and read
    # both derive it from the symbol), so lossiness is fine.
    return symbol.upper().replace("/", "-").replace("\\", "-").replace(".", "_")


class ParquetCache:
    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = Path(cache_dir)

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

    def get_or_fetch(
        self,
        symbol: str,
        start,
        end,
        source: BarSource,
        *,
        refresh: bool = False,
    ) -> pd.DataFrame:
        """Return bars for [start, end], fetching + caching only if not covered."""
        start_ts = pd.Timestamp(start).normalize()
        end_ts = pd.Timestamp(end).normalize()
        cached = None if refresh else self.read(symbol)
        if (
            cached is not None
            and len(cached)
            and cached.index.min() <= start_ts
            and cached.index.max() >= end_ts
        ):
            return cached.loc[start_ts:end_ts]
        merged = self.update(symbol, source.daily_bars(symbol, start, end))
        return merged.loc[start_ts:end_ts]
