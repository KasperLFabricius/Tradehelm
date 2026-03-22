"""Lightweight strategy metadata registry for Strategy Lab."""
from __future__ import annotations

from dataclasses import dataclass

from tradehelm.config.models import AppConfig


@dataclass(frozen=True)
class StrategyCatalogEntry:
    strategy_id: str
    display_name: str
    description: str
    regime_type: str
    supported_intervals: list[str]
    defaults: dict


def strategy_catalog(base_config: AppConfig | None = None) -> list[StrategyCatalogEntry]:
    cfg = base_config or AppConfig()
    return [
        StrategyCatalogEntry(
            strategy_id="orb",
            display_name="Opening Range Breakout",
            description="Classic opening-range breakout strategy with deterministic exits.",
            regime_type="breakout",
            supported_intervals=["1min", "5min", "15min", "30min"],
            defaults=cfg.strategies.orb.model_dump(),
        ),
        StrategyCatalogEntry(
            strategy_id="gap_orb",
            display_name="Gap-filtered ORB",
            description="ORB variant requiring minimum opening gap and opening-range strength.",
            regime_type="breakout",
            supported_intervals=["1min", "5min", "15min", "30min"],
            defaults=cfg.strategies.gap_orb.model_dump(),
        ),
        StrategyCatalogEntry(
            strategy_id="vwap",
            display_name="VWAP Continuation",
            description="Trend-continuation strategy around VWAP pullbacks.",
            regime_type="continuation",
            supported_intervals=["1min", "5min", "15min"],
            defaults=cfg.strategies.vwap.model_dump(),
        ),
        StrategyCatalogEntry(
            strategy_id="vwap_mean_reversion",
            display_name="VWAP Mean Reversion",
            description="Fades extended moves away from VWAP once reversion confirms.",
            regime_type="mean_reversion",
            supported_intervals=["1min", "5min", "15min"],
            defaults=cfg.strategies.vwap_mean_reversion.model_dump(),
        ),
    ]


def strategy_catalog_payload(base_config: AppConfig | None = None) -> list[dict]:
    return [entry.__dict__ for entry in strategy_catalog(base_config)]
