"""Import + wiring guards for scripts/run_research.py (the study is never run in CI;
this just catches import/signature breakage and checks the panel assembly)."""

import pandas as pd
import pytest

from scripts import run_research


class _StubCache:
    def __init__(self, frames):
        self._frames = frames

    def read(self, symbol):
        return self._frames.get(symbol)


def _bars():
    idx = pd.bdate_range("2020-01-01", periods=3)
    return pd.DataFrame(
        {"open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "adj_close": 1.0, "volume": 1},
        index=idx,
    )


def test_arg_defaults():
    args = run_research._parse_args([])
    assert args.start == "2005-01-01"
    assert args.candidates == "a,b,c"
    assert args.benchmark == "SPY"


def test_build_panel_includes_benchmark_and_skips_missing():
    cache = _StubCache({"AAA": _bars(), "SPY": _bars()})  # BBB absent
    panel = run_research.build_panel(cache, ["AAA", "BBB"], "SPY")
    assert set(panel) == {"AAA", "SPY"}  # BBB skipped, SPY always present


def test_build_panel_requires_benchmark_in_cache():
    cache = _StubCache({"AAA": _bars()})  # no SPY
    with pytest.raises(SystemExit):
        run_research.build_panel(cache, ["AAA"], "SPY")


def test_fx_constant_default():
    assert run_research._fx(run_research._parse_args([])) == pytest.approx(6.9)


def test_fx_csv_validated(tmp_path):
    good = tmp_path / "fx.csv"
    good.write_text("date,rate\n2020-01-02,6.8\n2020-01-01,6.9\n", encoding="utf-8")
    series = run_research._fx(run_research._parse_args(["--fx-csv", str(good)]))
    assert list(series.index) == [pd.Timestamp("2020-01-01"), pd.Timestamp("2020-01-02")]  # sorted
    assert list(series.to_numpy()) == [6.9, 6.8]
    bad = tmp_path / "bad.csv"
    bad.write_text("date,rate\n2020-01-01,-1\n", encoding="utf-8")  # non-positive rate
    with pytest.raises(SystemExit):
        run_research._fx(run_research._parse_args(["--fx-csv", str(bad)]))
