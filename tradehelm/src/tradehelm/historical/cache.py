"""Local CSV cache for historical datasets."""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import desc, select
from sqlalchemy.orm import sessionmaker

from tradehelm.historical.interfaces import DividendEvent, SplitEvent
from tradehelm.historical.intervals import ensure_supported_interval
from tradehelm.persistence.db import HistoricalDatasetRecord
from tradehelm.trading_engine.types import Bar


@dataclass(slots=True)
class DatasetRef:
    cache_key: str
    bars_path: Path
    splits_path: Path
    dividends_path: Path


class HistoricalCache:
    def __init__(self, session_factory: sessionmaker, cache_dir: str = "./historical_cache") -> None:
        self.session_factory = session_factory
        self.cache_dir = Path(cache_dir).expanduser().resolve()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def make_cache_key(
        self,
        provider: str,
        symbol: str,
        interval: str,
        start_date: date,
        end_date: date,
        adjusted: bool,
    ) -> str:
        normalized_interval = ensure_supported_interval(interval)
        raw = (
            f"{provider}|{symbol.upper()}|{normalized_interval}|{start_date.isoformat()}|"
            f"{end_date.isoformat()}|{int(adjusted)}"
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]

    def dataset_paths(self, cache_key: str) -> DatasetRef:
        base = self.cache_dir / cache_key
        base.mkdir(parents=True, exist_ok=True)
        return DatasetRef(
            cache_key=cache_key,
            bars_path=base / "bars.csv",
            splits_path=base / "splits.csv",
            dividends_path=base / "dividends.csv",
        )

    def write_dataset(
        self,
        provider: str,
        symbol: str,
        interval: str,
        start_date: date,
        end_date: date,
        adjusted: bool,
        bars: list[Bar],
        splits: list[SplitEvent],
        dividends: list[DividendEvent],
    ) -> DatasetRef:
        normalized_interval = ensure_supported_interval(interval)
        key = self.make_cache_key(provider, symbol, normalized_interval, start_date, end_date, adjusted)
        paths = self.dataset_paths(key)

        with paths.bars_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["timestamp", "symbol", "open", "high", "low", "close", "volume"])
            writer.writeheader()
            for bar in bars:
                writer.writerow(
                    {
                        "timestamp": bar.ts.isoformat(),
                        "symbol": bar.symbol,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.volume,
                    }
                )

        with paths.splits_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["symbol", "ex_date", "ratio_from", "ratio_to"])
            writer.writeheader()
            for item in splits:
                writer.writerow(
                    {
                        "symbol": item.symbol,
                        "ex_date": item.ex_date.isoformat(),
                        "ratio_from": item.ratio_from,
                        "ratio_to": item.ratio_to,
                    }
                )

        with paths.dividends_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["symbol", "ex_date", "amount"])
            writer.writeheader()
            for item in dividends:
                writer.writerow(
                    {
                        "symbol": item.symbol,
                        "ex_date": item.ex_date.isoformat(),
                        "amount": item.amount,
                    }
                )

        with self.session_factory() as session:
            row = session.scalar(select(HistoricalDatasetRecord).where(HistoricalDatasetRecord.cache_key == key))
            if row is None:
                row = HistoricalDatasetRecord(cache_key=key)
                session.add(row)
            row.provider = provider
            row.symbol = symbol
            row.interval = normalized_interval
            row.start_date = start_date.isoformat()
            row.end_date = end_date.isoformat()
            row.adjusted = int(adjusted)
            row.bars_path = str(paths.bars_path)
            row.splits_path = str(paths.splits_path)
            row.dividends_path = str(paths.dividends_path)
            session.commit()
        return paths

    def load_bars(self, cache_key: str) -> list[Bar]:
        ref = self.dataset_paths(cache_key)
        if not ref.bars_path.exists():
            return []
        with ref.bars_path.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        out: list[Bar] = []
        for row in rows:
            out.append(
                Bar(
                    ts=datetime.fromisoformat(row["timestamp"]),
                    symbol=row["symbol"],
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
        return sorted(out, key=lambda b: (b.ts, b.symbol))

    def find_dataset(
        self,
        provider: str,
        symbol: str,
        interval: str,
        start_date: date,
        end_date: date,
        adjusted: bool,
    ) -> HistoricalDatasetRecord | None:
        key = self.make_cache_key(provider, symbol, interval, start_date, end_date, adjusted)
        with self.session_factory() as session:
            return session.scalar(select(HistoricalDatasetRecord).where(HistoricalDatasetRecord.cache_key == key))

    def list_datasets(self) -> list[dict]:
        with self.session_factory() as session:
            rows = session.scalars(select(HistoricalDatasetRecord).order_by(desc(HistoricalDatasetRecord.id))).all()
            return [
                {
                    "id": row.id,
                    "cache_key": row.cache_key,
                    "provider": row.provider,
                    "symbol": row.symbol,
                    "interval": row.interval,
                    "start_date": row.start_date,
                    "end_date": row.end_date,
                    "adjusted": bool(row.adjusted),
                    "bars_path": row.bars_path,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ]

    def list_cache_files(self) -> list[dict]:
        out: list[dict] = []
        for path in sorted(self.cache_dir.glob("**/*.csv")):
            out.append({"path": str(path), "size": path.stat().st_size})
        return out
