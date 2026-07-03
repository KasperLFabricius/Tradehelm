"""Deterministic technical indicators used by the v1 strategy candidates.

Every function is a pure transform of a price frame or close series and returns a
pandas Series aligned to the input index. Values are computed only from data at or
before each row (rolling / backward-looking), so a strategy that reads ``.iloc[-1]``
of the returned series on its decision date sees no future information - the
anti-lookahead guarantee is structural, not enforced at runtime.

Insufficient history yields NaN (via ``min_periods``); callers must treat NaN as
"no signal" rather than a number. See docs/STRATEGY_SPEC.md for where each is used.
"""

from __future__ import annotations

import pandas as pd


def sma(close: pd.Series, window: int) -> pd.Series:
    """Simple moving average of `close` over `window` sessions (NaN until full)."""
    if window < 1:
        raise ValueError("window must be >= 1")
    return close.rolling(window, min_periods=window).mean()


def rsi(close: pd.Series, window: int) -> pd.Series:
    """Wilder's RSI over `window` sessions, in [0, 100].

    Uses Wilder's exponential smoothing (alpha = 1/window, seeded from the first
    delta). A window with no losses returns 100; with no gains, 0. NaN until at
    least `window` deltas exist.
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0.0, pd.NA)  # avoid a divide-by-zero warning
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0.0, 100.0)  # no losses in the window -> RSI 100
    out = out.where((avg_gain != 0.0) | (avg_loss != 0.0), 50.0)  # perfectly flat -> neutral
    warm = avg_gain.isna() | avg_loss.isna()  # keep NaN over the smoothing warm-up
    return out.where(~warm)


def true_range(frame: pd.DataFrame) -> pd.Series:
    """Wilder's true range: max(H-L, |H-prevC|, |L-prevC|). First row is H-L."""
    high, low, close = frame["high"], frame["low"], frame["close"]
    prev_close = close.shift(1)
    hl = high - low
    hc = (high - prev_close).abs()
    lc = (low - prev_close).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    tr.iloc[0] = hl.iloc[0] if len(hl) else tr.iloc[0]  # no prev close on the first bar
    return tr


def atr(frame: pd.DataFrame, window: int) -> pd.Series:
    """Average true range via Wilder smoothing over `window` sessions."""
    if window < 1:
        raise ValueError("window must be >= 1")
    tr = true_range(frame)
    return tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def highest_close(close: pd.Series, window: int) -> pd.Series:
    """Rolling maximum close over `window` sessions (inclusive of the current bar)."""
    if window < 1:
        raise ValueError("window must be >= 1")
    return close.rolling(window, min_periods=window).max()


def lowest_close(close: pd.Series, window: int) -> pd.Series:
    """Rolling minimum close over `window` sessions (inclusive of the current bar)."""
    if window < 1:
        raise ValueError("window must be >= 1")
    return close.rolling(window, min_periods=window).min()


def trailing_return(close: pd.Series, window: int, skip: int = 0) -> pd.Series:
    """Return over `window` sessions ending `skip` sessions ago.

    With skip=0 this is close[t]/close[t-window]-1. With skip=5, window=126 (a
    momentum score that ignores the most recent week), it is
    close[t-5]/close[t-5-126]-1. NaN where either endpoint is missing.
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    if skip < 0:
        raise ValueError("skip must be >= 0")
    end = close.shift(skip)
    start = close.shift(skip + window)
    return end / start - 1.0


def median_dollar_volume(frame: pd.DataFrame, window: int) -> pd.Series:
    """Rolling median of the adjustment-invariant dollar_volume over `window`.

    Uses the precomputed `dollar_volume` column (raw close * raw volume) so the
    liquidity filter is not distorted by splits. NaN until `window` bars exist.
    """
    if window < 1:
        raise ValueError("window must be >= 1")
    if "dollar_volume" not in frame.columns:
        raise KeyError("frame has no 'dollar_volume' column (use adjusted_ohlc output)")
    return frame["dollar_volume"].rolling(window, min_periods=window).median()
