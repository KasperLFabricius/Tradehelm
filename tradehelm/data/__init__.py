"""Data layer: bar sources, local cache, trading calendar, and universe.

Public API:
    BarSource (protocol), YFinanceSource     - fetch daily bars
    ParquetCache                             - local per-symbol cache
    TradingCalendar                          - NYSE sessions + arithmetic
    Universe                                 - point-in-time S&P 500 membership
    ensure_bar_frame, BAR_COLUMNS            - canonical bar schema
    DataError, EmptyDataError, DataGapError  - failures

See docs/PLAN.md Phase 1 and docs/ARCHITECTURE.md section 3.
"""

from __future__ import annotations

from .cache import ParquetCache
from .calendar import TradingCalendar
from .schema import (
    BAR_COLUMNS,
    DataError,
    DataGapError,
    EmptyDataError,
    ensure_bar_frame,
)
from .sources import BarSource, YFinanceSource
from .universe import Universe

__all__ = [
    "BAR_COLUMNS",
    "BarSource",
    "DataError",
    "DataGapError",
    "EmptyDataError",
    "ParquetCache",
    "TradingCalendar",
    "Universe",
    "YFinanceSource",
    "ensure_bar_frame",
]
