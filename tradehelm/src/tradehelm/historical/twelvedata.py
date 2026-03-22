"""Twelve Data historical provider implementation."""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta, timezone

import requests

from tradehelm.historical.interfaces import DividendEvent, HistoricalDataProvider, SUPPORTED_INTERVAL, SplitEvent
from tradehelm.trading_engine.types import Bar


class HistoricalProviderError(Exception):
    """Provider-level error for API-safe mapping."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class TwelveDataHistoricalProvider(HistoricalDataProvider):
    """Fetches bars and corporate actions from Twelve Data."""

    name = "twelvedata"

    def __init__(
        self,
        api_key: str | None = None,
        api_key_env: str = "TWELVE_DATA_API_KEY",
        base_url: str = "https://api.twelvedata.com",
        timeout_seconds: int = 20,
        max_retries: int = 2,
        bars_chunk_days: int = 30,
    ) -> None:
        self.api_key = api_key
        self.api_key_env = api_key_env
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.bars_chunk_days = bars_chunk_days

    def _require_api_key(self) -> str:
        resolved = self.api_key or os.getenv(self.api_key_env)
        if not resolved:
            raise HistoricalProviderError("missing_provider_key", "Twelve Data API key not configured.")
        return resolved

    def _request(self, path: str, params: dict[str, str]) -> dict:
        api_key = self._require_api_key()
        merged = dict(params)
        merged["apikey"] = api_key
        url = f"{self.base_url}/{path.lstrip('/')}"
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                response = requests.get(url, params=merged, timeout=self.timeout_seconds)
                if response.status_code >= 500 and attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict) and payload.get("status") == "error":
                    raise HistoricalProviderError("provider_failure", payload.get("message") or "Twelve Data request failed.")
                return payload
            except requests.RequestException as exc:
                last_error = exc
                if attempt < self.max_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                break
        raise HistoricalProviderError("provider_failure", f"Twelve Data request failed: {last_error}")

    def fetch_bars(self, symbol: str, interval: str, start_date: date, end_date: date) -> list[Bar]:
        if interval != SUPPORTED_INTERVAL:
            raise HistoricalProviderError("unsupported_interval", f"Unsupported interval: {interval}")
        start_dt = datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc)
        end_dt = datetime.combine(end_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        bars_by_ts: dict[datetime, Bar] = {}

        cursor = start_dt
        while cursor < end_dt:
            chunk_end = min(cursor + timedelta(days=self.bars_chunk_days), end_dt)
            payload = self._request(
                "time_series",
                {
                    "symbol": symbol,
                    "interval": interval,
                    "start_date": cursor.strftime("%Y-%m-%d %H:%M:%S"),
                    "end_date": chunk_end.strftime("%Y-%m-%d %H:%M:%S"),
                    "format": "JSON",
                    "order": "ASC",
                    "timezone": "UTC",
                    "outputsize": "5000",
                },
            )
            values = payload.get("values", []) if isinstance(payload, dict) else []
            for row in values:
                ts = datetime.fromisoformat(row["datetime"]).replace(tzinfo=timezone.utc)
                bars_by_ts[ts] = Bar(
                    ts=ts,
                    symbol=symbol,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row.get("volume", 0.0) or 0.0),
                )
            if chunk_end >= end_dt:
                break
            cursor = chunk_end - timedelta(minutes=5)

        bars = sorted(bars_by_ts.values(), key=lambda b: b.ts)
        return bars

    def fetch_splits(self, symbol: str, start_date: date, end_date: date) -> list[SplitEvent]:
        payload = self._request(
            "splits",
            {
                "symbol": symbol,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "format": "JSON",
            },
        )
        values = payload.get("splits", []) if isinstance(payload, dict) else []
        events: list[SplitEvent] = []
        for row in values:
            events.append(
                SplitEvent(
                    symbol=symbol,
                    ex_date=date.fromisoformat(row["date"]),
                    ratio_from=float(row.get("ratio_from", 1.0) or 1.0),
                    ratio_to=float(row.get("ratio_to", 1.0) or 1.0),
                )
            )
        return sorted(events, key=lambda e: e.ex_date)

    def fetch_dividends(self, symbol: str, start_date: date, end_date: date) -> list[DividendEvent]:
        payload = self._request(
            "dividends",
            {
                "symbol": symbol,
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "format": "JSON",
            },
        )
        values = payload.get("dividends", []) if isinstance(payload, dict) else []
        events: list[DividendEvent] = []
        for row in values:
            events.append(
                DividendEvent(
                    symbol=symbol,
                    ex_date=date.fromisoformat(row["ex_date"]),
                    amount=float(row.get("amount", 0.0) or 0.0),
                )
            )
        return sorted(events, key=lambda e: e.ex_date)
