"""CSV replay market data provider."""
from __future__ import annotations

from collections.abc import Iterable

import pandas as pd

from tradehelm.providers.interfaces import MarketDataProvider
from tradehelm.trading_engine.types import Bar


class ReplayMarketDataProvider(MarketDataProvider):
    """Loads OHLCV CSV and yields bars sorted by timestamp."""

    def __init__(self, replay_speed: float = 1.0) -> None:
        self.replay_speed = replay_speed
        self._df: pd.DataFrame | None = None

    def load(self, path: str) -> None:
        df = pd.read_csv(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
        self._df = df.sort_values(["timestamp", "symbol"]).reset_index(drop=True)

    def bars(self) -> Iterable[Bar]:
        if self._df is None:
            return []
        for _, row in self._df.iterrows():
            yield Bar(
                ts=row["timestamp"].to_pydatetime(),
                symbol=row["symbol"],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
