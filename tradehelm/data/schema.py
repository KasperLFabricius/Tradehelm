"""Canonical daily-bar schema and validation.

A bar frame is a pandas DataFrame with columns BAR_COLUMNS and a tz-naive
DatetimeIndex normalised to calendar dates (UTC midnight, tz dropped), sorted
ascending with no duplicate dates. All sources and the cache pass their output
through ensure_bar_frame so the rest of the system sees one shape.
"""

from __future__ import annotations

import pandas as pd

BAR_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "adj_close", "volume")
_PRICE_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close")


class DataError(Exception):
    """Base class for data-layer failures."""


class EmptyDataError(DataError):
    """A source or cache returned no usable rows (fail loud; never fabricate)."""


class DataGapError(DataError):
    """Expected trading sessions are missing from a bar frame."""


_COLUMN_ALIASES = {
    "adj close": "adj_close",
    "adjclose": "adj_close",
    "adjusted_close": "adj_close",
}


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    renamed = {}
    for col in df.columns:
        key = str(col).strip().lower()
        renamed[col] = _COLUMN_ALIASES.get(key, key.replace(" ", "_"))
    return df.rename(columns=renamed)


def ensure_bar_frame(df: pd.DataFrame | None, *, symbol: str | None = None) -> pd.DataFrame:
    """Validate and normalise a raw frame into the canonical bar schema.

    Raises EmptyDataError if there are no usable rows and DataError if required
    columns are absent. Never silently returns a partial/empty frame.
    """
    who = f" for {symbol!r}" if symbol else ""
    if df is None or len(df) == 0:
        raise EmptyDataError(f"No bars{who}")

    out = _normalize_columns(df)
    missing = [c for c in BAR_COLUMNS if c not in out.columns]
    if missing:
        raise DataError(f"Bar frame{who} missing columns: {missing}")
    out = out.loc[:, list(BAR_COLUMNS)].copy()

    idx = pd.DatetimeIndex(pd.to_datetime(out.index))
    if idx.tz is not None:
        idx = idx.tz_convert("UTC").tz_localize(None)
    out.index = idx.normalize()
    out.index.name = "date"

    for col in BAR_COLUMNS:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out[~out.index.duplicated(keep="last")].sort_index()
    out = out.dropna(subset=list(_PRICE_COLUMNS), how="all")
    if len(out) == 0:
        raise EmptyDataError(f"All bars empty{who}")
    return out
