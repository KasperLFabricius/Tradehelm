"""Historical orchestration service: validation, fetch, cache, prepare."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from tradehelm.historical.adjustments import apply_corporate_action_adjustments
from tradehelm.historical.cache import HistoricalCache
from tradehelm.historical.intervals import ensure_supported_interval, supported_intervals
from tradehelm.historical.twelvedata import HistoricalProviderError, TwelveDataHistoricalProvider


class HistoricalValidationError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(slots=True)
class HistoricalRequest:
    symbols: list[str]
    start_date: date
    end_date: date
    interval: str
    adjusted: bool


class HistoricalService:
    def __init__(self, cache: HistoricalCache, provider: TwelveDataHistoricalProvider) -> None:
        self.cache = cache
        self.provider = provider

    def validate_request(self, req: HistoricalRequest) -> None:
        symbols = [s.strip().upper() for s in req.symbols if s.strip()]
        if not symbols:
            raise HistoricalValidationError("invalid_symbols", "At least one symbol is required.")
        for symbol in symbols:
            if not symbol.isalpha() or len(symbol) > 8:
                raise HistoricalValidationError("invalid_symbols", f"Invalid US equity symbol: {symbol}")
        if req.end_date < req.start_date:
            raise HistoricalValidationError("invalid_date_range", "end_date must be on or after start_date.")
        try:
            ensure_supported_interval(req.interval)
        except ValueError:
            allowed = ", ".join(supported_intervals())
            raise HistoricalValidationError("unsupported_interval", f"Unsupported interval: {req.interval}. Supported intervals: {allowed}.")

    def normalized_interval(self, interval: str) -> str:
        try:
            return ensure_supported_interval(interval)
        except ValueError:
            allowed = ", ".join(supported_intervals())
            raise HistoricalValidationError("unsupported_interval", f"Unsupported interval: {interval}. Supported intervals: {allowed}.")

    def fetch_and_cache(self, req: HistoricalRequest, use_existing: bool = True) -> dict:
        self.validate_request(req)
        interval = self.normalized_interval(req.interval)
        normalized_symbols = sorted({s.strip().upper() for s in req.symbols if s.strip()})
        cached = []
        downloaded = []
        for symbol in normalized_symbols:
            existing = self.cache.find_dataset(
                self.provider.name,
                symbol,
                interval,
                req.start_date,
                req.end_date,
                req.adjusted,
            )
            if existing is not None and use_existing:
                cached.append({"symbol": symbol, "cache_key": existing.cache_key, "reused": True})
                continue

            bars = self.provider.fetch_bars(symbol, interval, req.start_date, req.end_date)
            deduped: dict = {(bar.ts, bar.symbol): bar for bar in bars}
            bars = sorted(deduped.values(), key=lambda b: (b.ts, b.symbol))
            if not bars:
                raise HistoricalValidationError("empty_fetch_result", f"No bars returned for symbol {symbol}.")
            splits = self.provider.fetch_splits(symbol, req.start_date, req.end_date)
            dividends = self.provider.fetch_dividends(symbol, req.start_date, req.end_date)
            stored_bars = apply_corporate_action_adjustments(bars, splits, dividends, apply_dividends=False) if req.adjusted else bars
            ref = self.cache.write_dataset(
                provider=self.provider.name,
                symbol=symbol,
                interval=interval,
                start_date=req.start_date,
                end_date=req.end_date,
                adjusted=req.adjusted,
                bars=stored_bars,
                splits=splits,
                dividends=dividends,
            )
            downloaded.append(
                {
                    "symbol": symbol,
                    "cache_key": ref.cache_key,
                    "bars": len(stored_bars),
                    "splits": len(splits),
                    "dividends": len(dividends),
                    "adjusted": req.adjusted,
                }
            )

        return {
            "provider": self.provider.name,
            "interval": interval,
            "start_date": req.start_date.isoformat(),
            "end_date": req.end_date.isoformat(),
            "adjusted": req.adjusted,
            "symbols": normalized_symbols,
            "downloaded": downloaded,
            "cached": cached,
        }

    @staticmethod
    def map_error(exc: Exception) -> tuple[int, dict]:
        if isinstance(exc, HistoricalValidationError):
            return 400, {"code": exc.code, "message": str(exc)}
        if isinstance(exc, HistoricalProviderError):
            code = exc.code
            status = 400 if code in {"missing_provider_key", "unsupported_interval"} else 502
            return status, {"code": code, "message": str(exc)}
        return 500, {"code": "historical_internal_error", "message": "Historical data request failed."}
