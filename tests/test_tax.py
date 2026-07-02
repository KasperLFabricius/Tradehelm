"""Danish aktieindkomst tax-model tests.

Written FIRST from the worked examples E1-E5 in docs/COSTS_AND_TAX.md
(CLAUDE.md rule 6). All amounts are DKK; the ledger is FX-inclusive (callers pass
per-share cost/proceeds already converted at the trade-date USD/DKK rate).
"""

import pytest

from tradehelm.backtest import DanishTaxLedger, progressive_tax

THRESHOLDS = {2024: 61000.0, 2025: 67500.0, 2026: 79400.0}


def _ledger():
    return DanishTaxLedger(thresholds=THRESHOLDS, rate_low=0.27, rate_high=0.42)


def test_e1_average_cost_basis():
    ledger = _ledger()
    ledger.buy("X", 100, 350.0)  # 50 USD * 7.00
    ledger.buy("X", 100, 420.0)  # 60 USD * 7.00  -> avg 385
    gain = ledger.sell("X", 100, 490.0, 2026)  # 70 USD * 7.00
    assert gain == pytest.approx(10_500.0)  # (490 - 385) * 100
    assert ledger.average_cost("X") == pytest.approx(385.0)  # remaining 100 keep basis 385
    assert ledger.shares("X") == pytest.approx(100)


def test_e2_progression_bands():
    # 0.27 * 79,400 + 0.42 * 20,600
    assert progressive_tax(100_000.0, 79_400.0, 0.27, 0.42) == pytest.approx(30_090.0)


def test_e3_ring_fenced_loss_carries_forward():
    ledger = _ledger()
    ledger.buy("A", 100, 1000.0)
    ledger.sell("A", 100, 600.0, 2025)  # (600 - 1000) * 100 = -40,000
    assert ledger.close_year(2025) == pytest.approx(0.0)
    assert ledger.carried_loss == pytest.approx(40_000.0)

    ledger.buy("B", 100, 1000.0)
    ledger.sell("B", 100, 2000.0, 2026)  # +100,000
    tax = ledger.close_year(2026)
    assert tax == pytest.approx(16_200.0)  # taxable 60,000 (after carry), all at 27%
    assert ledger.carried_loss == pytest.approx(0.0)


def test_e4_fx_movement_is_taxable():
    ledger = _ledger()
    ledger.buy("X", 100, 325.0)  # 50 USD * 6.50
    gain = ledger.sell("X", 100, 350.0, 2026)  # 50 USD * 7.00 (price flat in USD)
    assert gain == pytest.approx(2_500.0)  # pure FX gain


def test_e5_loss_never_leaks():
    ledger = _ledger()
    ledger.buy("X", 100, 1000.0)
    ledger.sell("X", 100, 500.0, 2026)  # -50,000
    assert ledger.close_year(2026) == pytest.approx(0.0)  # never negative tax
    assert ledger.carried_loss == pytest.approx(50_000.0)


def test_dividends_are_aktieindkomst():
    ledger = _ledger()
    ledger.add_dividend(10_000.0, 2026)
    assert ledger.close_year(2026) == pytest.approx(0.27 * 10_000.0)


def test_partial_carry_consumption():
    ledger = _ledger()
    ledger.add_dividend(-30_000.0, 2025)  # a loss
    ledger.close_year(2025)
    assert ledger.carried_loss == pytest.approx(30_000.0)
    ledger.add_dividend(10_000.0, 2026)  # smaller than the carry
    assert ledger.close_year(2026) == pytest.approx(0.0)  # fully sheltered
    assert ledger.carried_loss == pytest.approx(20_000.0)  # 30k - 10k remains


def test_oversell_raises():
    ledger = _ledger()
    ledger.buy("X", 100, 100.0)
    with pytest.raises(ValueError):
        ledger.sell("X", 101, 200.0, 2026)


def test_non_positive_quantities_raise():
    ledger = _ledger()
    ledger.buy("X", 100, 100.0)
    with pytest.raises(ValueError):
        ledger.sell("X", -50, 200.0, 2026)  # sign bug must fail loud, not grow position
    with pytest.raises(ValueError):
        ledger.sell("X", 0, 200.0, 2026)
    with pytest.raises(ValueError):
        ledger.buy("X", -10, 100.0)
    assert ledger.shares("X") == pytest.approx(100)  # unchanged


def test_close_year_is_idempotent():
    ledger = _ledger()
    ledger.add_dividend(-30_000.0, 2025)
    assert ledger.close_year(2025) == pytest.approx(0.0)
    assert ledger.carried_loss == pytest.approx(30_000.0)
    # Re-closing must not double-count the loss.
    assert ledger.close_year(2025) == pytest.approx(0.0)
    assert ledger.carried_loss == pytest.approx(30_000.0)

    ledger.add_dividend(100_000.0, 2026)
    first = ledger.close_year(2026)  # taxable 70,000 (after 30k carry)
    second = ledger.close_year(2026)
    assert first == pytest.approx(second)
    assert ledger.carried_loss == pytest.approx(0.0)  # not re-consumed


def test_missing_threshold_year_raises():
    ledger = _ledger()
    ledger.add_dividend(100_000.0, 2099)
    with pytest.raises(KeyError):
        ledger.close_year(2099)
