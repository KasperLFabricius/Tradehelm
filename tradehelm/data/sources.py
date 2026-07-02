"""Bar data sources.

BarSource is the protocol the rest of the system depends on. YFinanceSource is
the v1 implementation (free daily bars). The network call is injectable so the
parsing/retry logic is unit-tested without touching the network (CLAUDE.md: no
network in CI; never fabricate data).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

import pandas as pd

from .schema import DataError, EmptyDataError, ensure_bar_frame

Downloader = Callable[[str, object, object], pd.DataFrame]


class BarSource(Protocol):
    def daily_bars(self, symbol: str, start, end) -> pd.DataFrame:
        """Daily bars for [start, end], canonical schema (see schema.py)."""
        ...


def _flatten_yf(raw: pd.DataFrame) -> pd.DataFrame:
    """yfinance may return MultiIndex columns (field, ticker) even for one symbol."""
    df = raw.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _yahoo_symbol(symbol: str) -> str:
    """Canonical ticker -> Yahoo symbol. Yahoo uses '-' for share classes
    (BRK.B -> BRK-B); the canonical ticker is preserved everywhere else."""
    return symbol.replace(".", "-")


class YFinanceSource:
    """Daily bars from yfinance, with retry/backoff and fail-loud empties."""

    def __init__(
        self,
        downloader: Downloader | None = None,
        *,
        retries: int = 3,
        backoff: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if retries < 1:
            raise ValueError("retries must be >= 1")
        self._downloader = downloader
        self._retries = retries
        self._backoff = backoff
        self._sleep = sleep

    def _download(self, symbol: str, start, end) -> pd.DataFrame:
        # Translate to Yahoo conventions: '-' share classes, and an EXCLUSIVE end
        # (yfinance drops the end date) so our [start, end] contract is inclusive.
        yahoo_symbol = _yahoo_symbol(symbol)
        yahoo_end = pd.Timestamp(end).normalize() + pd.Timedelta(days=1)
        if self._downloader is not None:
            return self._downloader(yahoo_symbol, start, yahoo_end)
        import yfinance as yf

        return yf.download(
            yahoo_symbol,
            start=start,
            end=yahoo_end,
            interval="1d",
            auto_adjust=False,
            actions=False,
            progress=False,
            threads=False,
        )

    def daily_bars(self, symbol: str, start, end) -> pd.DataFrame:
        last_exc: Exception | None = None
        for attempt in range(self._retries):
            try:
                raw = self._download(symbol, start, end)
                return ensure_bar_frame(_flatten_yf(raw), symbol=symbol)
            except EmptyDataError as exc:
                last_exc = exc
            except Exception as exc:  # network/parse errors are retryable
                last_exc = exc
            if attempt < self._retries - 1:
                self._sleep(self._backoff * (2**attempt))
        # Preserve EmptyDataError (a genuinely empty response) so callers can tell
        # "no data" from "fetch failed" - the cache tolerates the former when
        # extending an already-cached (e.g. delisted) symbol.
        if isinstance(last_exc, EmptyDataError):
            raise last_exc
        raise DataError(
            f"Failed to fetch bars for {symbol!r} after {self._retries} attempts"
        ) from last_exc
