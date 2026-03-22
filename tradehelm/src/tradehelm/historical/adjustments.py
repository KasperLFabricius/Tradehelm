"""Client-side intraday adjustment helpers."""
from __future__ import annotations

from tradehelm.historical.interfaces import DividendEvent, SplitEvent
from tradehelm.trading_engine.types import Bar


def apply_corporate_action_adjustments(
    bars: list[Bar],
    splits: list[SplitEvent],
    dividends: list[DividendEvent],
    apply_dividends: bool = False,
) -> list[Bar]:
    """Apply reverse split and optional dividend adjustment to pre-event bars.

    Split handling: each split contributes factor = ratio_from / ratio_to and is
    applied to all bars strictly before the split ex-date.

    Dividend handling (optional): each dividend subtracts cash amount from OHLC on
    bars strictly before the ex-date. This is intentionally simple and documented.
    """

    adjusted: list[Bar] = []
    for bar in bars:
        price_factor = 1.0
        cash_offset = 0.0
        for split in splits:
            if bar.ts.date() < split.ex_date and split.ratio_to != 0:
                price_factor *= split.ratio_from / split.ratio_to
        if apply_dividends:
            for dividend in dividends:
                if bar.ts.date() < dividend.ex_date:
                    cash_offset += dividend.amount
        adjusted.append(
            Bar(
                ts=bar.ts,
                symbol=bar.symbol,
                open=(bar.open * price_factor) - cash_offset,
                high=(bar.high * price_factor) - cash_offset,
                low=(bar.low * price_factor) - cash_offset,
                close=(bar.close * price_factor) - cash_offset,
                volume=bar.volume,
            )
        )
    return adjusted
