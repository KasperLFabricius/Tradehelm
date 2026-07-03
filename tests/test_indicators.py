"""Unit tests for the technical indicators (tradehelm/strategy/indicators.py).

Values are hand-computed where practical; the anti-lookahead property is checked
structurally (an indicator at row k depends only on rows <= k)."""

import numpy as np
import pandas as pd
import pytest

from tradehelm.strategy import indicators as ind


def _series(values):
    return pd.Series(values, index=pd.bdate_range("2020-01-01", periods=len(values)), dtype=float)


def test_sma_matches_manual_and_warms_up():
    s = _series([1, 2, 3, 4, 5])
    out = ind.sma(s, 3)
    assert out.iloc[:2].isna().all()  # first two rows lack a full window
    assert out.iloc[2] == pytest.approx(2.0)  # (1+2+3)/3
    assert out.iloc[4] == pytest.approx(4.0)  # (3+4+5)/3


def test_rsi_hand_computed_wilder():
    # close [10,11,10,12] -> avg gains/losses via alpha=0.5, adjust=False.
    out = ind.rsi(_series([10, 11, 10, 12]), 2)
    assert out.iloc[0] != out.iloc[0] and out.iloc[1] != out.iloc[1]  # NaN warm-up
    assert out.iloc[2] == pytest.approx(50.0)
    assert out.iloc[3] == pytest.approx(83.33333, abs=1e-4)


def test_rsi_degenerate_all_gains_and_all_losses():
    up = ind.rsi(_series(list(range(1, 20))), 2)
    assert up.dtype == float  # stays float64 even when all-gains (no object upcast)
    assert up.dropna().iloc[-1] == pytest.approx(100.0)  # never a down day -> 100
    down = ind.rsi(_series(list(range(20, 1, -1))), 2)
    assert down.dropna().iloc[-1] == pytest.approx(0.0)  # never an up day -> 0
    assert (up.dropna() <= 100.0).all() and (up.dropna() >= 0.0).all()


def test_true_range_uses_prev_close():
    f = pd.DataFrame({"high": [10, 12, 11.0], "low": [8, 9, 9.0], "close": [9, 11, 10.0]})
    tr = ind.true_range(f)
    assert list(tr) == [2.0, 3.0, 2.0]  # first is H-L; then max(H-L, |H-pC|, |L-pC|)


def test_atr_is_positive_and_warms_up():
    f = pd.DataFrame(
        {
            "high": np.arange(1, 30) + 0.5,
            "low": np.arange(1, 30) - 0.5,
            "close": np.arange(1, 30).astype(float),
        }
    )
    a = ind.atr(f, 14)
    assert a.iloc[:13].isna().all()
    assert a.dropna().gt(0).all()


def test_highest_and_lowest_close():
    s = _series([3, 1, 4, 1, 5, 9, 2])
    assert ind.highest_close(s, 3).iloc[-1] == 9.0  # max(5,9,2)
    assert ind.lowest_close(s, 3).iloc[-1] == 2.0  # min(5,9,2)


def test_trailing_return_with_skip():
    s = _series([1, 2, 4, 8, 16, 32])
    # skip=1, window=2 at the last row: close[-2]/close[-4]-1 = 16/4-1 = 3.
    assert ind.trailing_return(s, 2, skip=1).iloc[-1] == pytest.approx(3.0)
    assert ind.trailing_return(s, 2, skip=0).iloc[-1] == pytest.approx(32 / 8 - 1)


def test_median_dollar_volume_uses_column_and_requires_it():
    idx = pd.bdate_range("2020-01-01", periods=25)
    f = pd.DataFrame({"dollar_volume": np.arange(1, 26).astype(float)}, index=idx)
    assert ind.median_dollar_volume(f, 20).iloc[-1] == pytest.approx(np.median(np.arange(6, 26)))
    with pytest.raises(KeyError):
        ind.median_dollar_volume(pd.DataFrame({"x": [1, 2]}), 2)


def test_indicators_have_no_lookahead():
    rng = np.random.default_rng(0)
    s = _series(100 + np.cumsum(rng.normal(0, 1, 260)))
    f = pd.DataFrame({"high": s + 1, "low": s - 1, "close": s, "dollar_volume": s * 1e6})
    k = 200
    for full, truncated in [
        (ind.sma(s, 20), ind.sma(s.iloc[: k + 1], 20)),
        (ind.rsi(s, 2), ind.rsi(s.iloc[: k + 1], 2)),
        (ind.atr(f, 14), ind.atr(f.iloc[: k + 1], 14)),
        (ind.highest_close(s, 55), ind.highest_close(s.iloc[: k + 1], 55)),
        (ind.trailing_return(s, 126, 5), ind.trailing_return(s.iloc[: k + 1], 126, 5)),
    ]:
        # The value at row k must not change when future rows are removed.
        assert full.iloc[k] == pytest.approx(truncated.iloc[k], nan_ok=True)


def test_bad_windows_rejected():
    s = _series([1, 2, 3])
    for fn in (ind.sma, ind.rsi, ind.highest_close, ind.lowest_close):
        with pytest.raises(ValueError):
            fn(s, 0)
    with pytest.raises(ValueError):
        ind.trailing_return(s, 1, skip=-1)
