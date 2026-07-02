"""Point-in-time S&P 500 membership.

Backed by a bundled `ticker,start_date,end_date` interval dataset (see
datasets/DATA_PROVENANCE.md for source, MIT license and limitations). An empty
end_date means "still a member"; a ticker may have several intervals (removed
then re-added), so membership is the union of its intervals.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .schema import DataError

_DATASET = Path(__file__).resolve().parent / "datasets" / "sp500_ticker_start_end.csv"
_REQUIRED = {"ticker", "start_date", "end_date"}


class Universe:
    def __init__(self, intervals: pd.DataFrame) -> None:
        missing = _REQUIRED - set(intervals.columns)
        if missing:
            raise DataError(f"Universe intervals missing columns: {sorted(missing)}")
        df = intervals.loc[:, ["ticker", "start_date", "end_date"]].copy()
        df["ticker"] = df["ticker"].astype(str).str.strip().str.upper()
        df["start_date"] = pd.to_datetime(df["start_date"]).dt.normalize()
        df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce").dt.normalize()
        if df["start_date"].isna().any():
            raise DataError("Universe has rows with an unparseable start_date")
        self._intervals = df.reset_index(drop=True)

    @classmethod
    def from_csv(cls, path: str | Path | None = None) -> Universe:
        source = Path(path) if path is not None else _DATASET
        if not source.exists():
            raise FileNotFoundError(f"Universe dataset not found: {source}")
        return cls(pd.read_csv(source))

    @classmethod
    def default(cls) -> Universe:
        """The bundled S&P 500 membership dataset."""
        return cls.from_csv(_DATASET)

    @staticmethod
    def default_dataset_path() -> Path:
        return _DATASET

    def members(self, on) -> list[str]:
        """Sorted tickers that were index members on the given date."""
        day = pd.Timestamp(on).normalize()
        df = self._intervals
        active = (df["start_date"] <= day) & (df["end_date"].isna() | (day <= df["end_date"]))
        return sorted(df.loc[active, "ticker"].unique().tolist())

    def all_symbols(self) -> list[str]:
        """Every ticker that has ever been a member (union over all intervals)."""
        return sorted(self._intervals["ticker"].unique().tolist())
