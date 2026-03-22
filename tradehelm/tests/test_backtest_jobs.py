import time
from datetime import date, datetime, timezone, timedelta

import pytest
from pydantic import ValidationError

from tradehelm.backtests.models import BacktestRequest
from tradehelm.config.models import AppConfig
from tradehelm.historical.backtest_runner import BacktestRunner
from tradehelm.historical.cache import HistoricalCache
from tradehelm.persistence.db import create_session_factory
from tradehelm.strategies.gap_orb import GapFilteredOrbStrategy
from tradehelm.strategies.vwap_mean_reversion import VwapMeanReversionStrategy
from tradehelm.trading_engine.types import Bar


def _seed_cache(cache: HistoricalCache, symbol: str = "AAPL", bars: int = 40) -> None:
    t0 = datetime(2026, 1, 2, 14, 30, tzinfo=timezone.utc)
    payload = [Bar(ts=t0 + timedelta(minutes=5 * i), symbol=symbol, open=100 + i * 0.01, high=100.2 + i * 0.01, low=99.8 + i * 0.01, close=100 + i * 0.01, volume=1000 + i) for i in range(bars)]
    cache.write_dataset(
        provider="twelvedata",
        symbol=symbol,
        interval="5min",
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 3),
        adjusted=True,
        bars=payload,
        splits=[],
        dividends=[],
    )


def _wait_for_status(runner: BacktestRunner, job_id: int, statuses: set[str], timeout: float = 4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = runner.get_job(job_id)
        assert job is not None
        if job["status"] in statuses:
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} not in {statuses}")


def test_job_enqueue_start_complete_and_progress(tmp_path):
    sf = create_session_factory(f"sqlite:///{tmp_path / 'jobs.db'}")
    cache = HistoricalCache(sf, cache_dir=str(tmp_path / "cache"))
    _seed_cache(cache)
    runner = BacktestRunner(sf, cache, AppConfig())

    job = runner.enqueue_job(
        "twelvedata",
        BacktestRequest(symbols=["AAPL"], start_date=date(2026, 1, 1), end_date=date(2026, 1, 3), interval="5min", adjusted=True),
    )
    done = _wait_for_status(runner, job["id"], {"COMPLETED"})
    assert done["run_id"] is not None
    assert float((done.get("progress") or {}).get("percent_complete", 0.0)) >= 100.0
    events = runner.list_job_events(job["id"])
    event_types = {e["event_type"] for e in events}
    assert "job_queued" in event_types
    assert "job_started" in event_types


def test_job_cancel_cooperative(tmp_path):
    sf = create_session_factory(f"sqlite:///{tmp_path / 'jobs_cancel.db'}")
    cache = HistoricalCache(sf, cache_dir=str(tmp_path / "cache"))
    _seed_cache(cache, bars=120)
    runner = BacktestRunner(sf, cache, AppConfig())
    job = runner.enqueue_job(
        "twelvedata",
        BacktestRequest(symbols=["AAPL"], start_date=date(2026, 1, 1), end_date=date(2026, 1, 3), interval="5min", adjusted=True),
    )
    runner.cancel_job(job["id"])
    done = _wait_for_status(runner, job["id"], {"CANCELLED", "COMPLETED"})
    assert done["status"] in {"CANCELLED", "COMPLETED"}


def test_run_snapshot_uses_per_run_overrides_without_global_mutation(tmp_path):
    cfg = AppConfig()
    original_orb = cfg.strategies.orb.opening_range_bars
    sf = create_session_factory(f"sqlite:///{tmp_path / 'overrides.db'}")
    cache = HistoricalCache(sf, cache_dir=str(tmp_path / "cache"))
    _seed_cache(cache)
    runner = BacktestRunner(sf, cache, cfg)

    result = runner.execute_request(
        "twelvedata",
        BacktestRequest(
            symbols=["AAPL"],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 3),
            interval="5min",
            adjusted=True,
            enabled_strategies=["orb"],
            strategy_params={"orb": {"opening_range_bars": 7}},
        ),
    )
    run = runner.get_run(result["run_id"])
    assert run is not None
    assert run["config"]["strategies"]["orb"]["opening_range_bars"] == 7
    assert cfg.strategies.orb.opening_range_bars == original_orb


def test_run_detail_and_compare_include_new_artifacts(tmp_path):
    sf = create_session_factory(f"sqlite:///{tmp_path / 'detail.db'}")
    cache = HistoricalCache(sf, cache_dir=str(tmp_path / "cache"))
    _seed_cache(cache)
    runner = BacktestRunner(sf, cache, AppConfig())
    r1 = runner.execute_request("twelvedata", BacktestRequest(symbols=["AAPL"], start_date=date(2026, 1, 1), end_date=date(2026, 1, 3), interval="5min", adjusted=True))
    r2 = runner.execute_request("twelvedata", BacktestRequest(symbols=["AAPL"], start_date=date(2026, 1, 1), end_date=date(2026, 1, 3), interval="5min", adjusted=True, enabled_strategies=["vwap"]))
    detail = runner.get_run(r1["run_id"])
    assert detail is not None
    assert "strategy_summary" in detail
    assert "trade_timeline" in detail
    compare = runner.compare_runs([r1["run_id"], r2["run_id"]])
    assert len(compare["runs"]) == 2
    assert "expectancy" in compare["runs"][0]


def test_gap_orb_requires_gap_condition():
    s = GapFilteredOrbStrategy()
    t0 = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
    assert s.on_bar(Bar(t0, "AAA", 100, 100.2, 99.8, 100.0, 1000)) == []
    assert s.on_bar(Bar(t0 + timedelta(minutes=1), "AAA", 100.0, 100.1, 99.9, 100.0, 1000)) == []
    intents = s.on_bar(Bar(t0 + timedelta(minutes=2), "AAA", 100.0, 100.6, 99.9, 100.5, 1000))
    assert intents == []


def test_vwap_mean_reversion_emits_signal_in_synthetic_data():
    s = VwapMeanReversionStrategy()
    t0 = datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc)
    s.on_bar(Bar(t0, "AAA", 100.0, 100.1, 99.9, 100.0, 1000))
    s.on_bar(Bar(t0 + timedelta(minutes=1), "AAA", 100.0, 100.1, 99.0, 99.2, 1000))
    intents = s.on_bar(Bar(t0 + timedelta(minutes=2), "AAA", 99.2, 99.8, 99.1, 99.7, 1000))
    assert any(i.reason in {"vwap_mr_revert_long", "vwap_mr_revert_short"} for i in intents)


def test_invalid_overrides_are_validated(tmp_path):
    sf = create_session_factory(f"sqlite:///{tmp_path / 'invalid_overrides.db'}")
    cache = HistoricalCache(sf, cache_dir=str(tmp_path / "cache"))
    _seed_cache(cache)
    runner = BacktestRunner(sf, cache, AppConfig())

    with pytest.raises(ValidationError):
        runner.validate_request_overrides(
            BacktestRequest(
                symbols=["AAPL"],
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 3),
                interval="5min",
                adjusted=True,
                strategy_params={"orb": {"max_bars_in_trade": 0}},
            )
        )
    with pytest.raises(ValidationError):
        runner.validate_request_overrides(
            BacktestRequest(
                symbols=["AAPL"],
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 3),
                interval="5min",
                adjusted=True,
                strategy_params={"orb": {"direction": "SIDEWAYS"}},
            )
        )
    with pytest.raises(ValidationError):
        runner.validate_request_overrides(
            BacktestRequest(
                symbols=["AAPL"],
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 3),
                interval="5min",
                adjusted=True,
                risk_overrides={"max_position_size": "bad"},
            )
        )
    with pytest.raises(ValueError):
        runner.validate_request_overrides(
            BacktestRequest(
                symbols=["AAPL"],
                start_date=date(2026, 1, 1),
                end_date=date(2026, 1, 3),
                interval="5min",
                adjusted=True,
                strategy_params={"unknown_strategy": {"foo": 1}},
            )
        )

    runner.validate_request_overrides(
        BacktestRequest(
            symbols=["AAPL"],
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 3),
            interval="5min",
            adjusted=True,
            strategy_params={"orb": {"max_bars_in_trade": 3}},
            risk_overrides={"max_trades_per_day": 10},
        )
    )


def test_worker_does_not_strand_late_enqueued_job(tmp_path):
    sf = create_session_factory(f"sqlite:///{tmp_path / 'race.db'}")
    cache = HistoricalCache(sf, cache_dir=str(tmp_path / "cache"))
    _seed_cache(cache, bars=30)
    runner = BacktestRunner(sf, cache, AppConfig())

    first = runner.enqueue_job("twelvedata", BacktestRequest(symbols=["AAPL"], start_date=date(2026, 1, 1), end_date=date(2026, 1, 3), interval="5min", adjusted=True))
    _wait_for_status(runner, first["id"], {"COMPLETED"})

    # enqueue shortly after queue drain to exercise worker idle/exit boundary
    time.sleep(0.12)
    second = runner.enqueue_job("twelvedata", BacktestRequest(symbols=["AAPL"], start_date=date(2026, 1, 1), end_date=date(2026, 1, 3), interval="5min", adjusted=True))
    outcome = _wait_for_status(runner, second["id"], {"RUNNING", "COMPLETED"}, timeout=4.0)
    assert outcome["status"] in {"RUNNING", "COMPLETED"}
