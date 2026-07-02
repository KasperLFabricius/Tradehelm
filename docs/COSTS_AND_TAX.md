# Tradehelm — Cost and Danish Tax Model Specification

Both models are used identically in backtest and in live accounting. All rates
live in `config.yaml`; anything marked TODO-VERIFY is a placeholder the owner
must confirm (Saxo DK price list / revisor) before Gate 7G.

## 1. Cost model

Per fill:

```
commission = max(min_commission, commission_rate * notional)   # per side
fill_price = open * (1 + side * (half_spread_bps + slippage_bps) / 10000)
```

Config (US stocks, Saxo DK Classic account — TODO-VERIFY every value):

| Key | Placeholder | Note |
|---|---|---|
| commission_rate_us | 0.0008 (0.08%) | TODO-VERIFY current Saxo DK rate |
| min_commission_us | 1.00 USD | TODO-VERIFY |
| half_spread_bps | 2.5 | liquid large caps; research must stress x2 |
| slippage_bps | 2.5 | market-on-open orders; stress x2 |
| fx_conversion_rate | 0.0025 (0.25%) | TODO-VERIFY; applied on DKK<->USD funding transfers only (decision D3), NOT per trade |
| custody_fee_annual | 0.0 | TODO-VERIFY whether Saxo DK charges custody on this account type |

FX handling: the account holds a USD sub-balance. The model charges
`fx_conversion_rate` when cash moves DKK->USD (initial funding, top-ups) and
USD->DKK (withdrawals, tax payments). A sensitivity line in research REPORT
must show the worst case (FX charged per trade) for comparison.

Dividends: credited in USD on ex-date +/- typical delay; Danish dividend tax on
US shares involves US withholding (15% treaty rate) creditable against Danish
tax — model net 27%/42% total with the withholding as a credit. TODO-VERIFY
treatment with revisor; keep it simple and conservative in v1.

## 2. Danish tax model (aktieindkomst — listed shares)

Scope: individual, unmarried rates, listed shares, realisation principle.
NOT naeringsbeskatning (owner action item: revisor confirms classification).

### Rules

1. **Average-cost basis (gennemsnitsmetoden):** per instrument, the cost basis
   of a sale is the average purchase price over ALL currently held shares of
   that instrument (not FIFO, not per-lot). Buys update the average; sells
   consume at the average.
2. **Realized gains/losses** accumulate per calendar year. Dividends (net
   concept per section 1) count as aktieindkomst in the same pot.
3. **Year-end settlement:** if net aktieindkomst > 0:
   `tax = 27% * min(net, threshold(year)) + 42% * max(0, net - threshold(year))`
   Prior-year carried losses are consumed first (they reduce net before the
   bands apply).
4. **Loss ring-fencing:** if net < 0, no relief against anything else; the loss
   carries forward indefinitely, offsetting future listed-share gains/dividends
   only.
5. **Timing:** tax is deducted from account equity at year-end in the backtest
   (configurable payment lag; real payment is the following year).
6. **Currency:** gains are computed in DKK. Each buy/sell converts USD amounts
   at that date's USD/DKK rate — FX movement is part of the taxable gain.
   Backtest uses a daily USD/DKK series (free source, e.g. ECB/FRED).

### Thresholds (config `tax.thresholds`, DKK)

| Year | Threshold | Status |
|---|---|---|
| 2024 | 61,000 | historical |
| 2025 | 67,500 | historical |
| 2026 | 79,400 | TODO-VERIFY — sources conflict (67.5k vs ~80k post-reform); revisor confirms |

Backtests over historical years use each year's historical threshold (fill the
table back to 2005 from skat.dk when implementing; they are public).

### Worked examples (unit tests are written from these first)

**E1 — average cost.** Buy 100 @ 50 USD, buy 100 @ 60 USD -> avg 55. Sell 100 @
70 -> gain 15 * 100 = 1,500 USD (converted to DKK at the sale-date rate for the
tax ledger; use a fixed test rate of 7.00 -> 10,500 DKK). Remaining 100 shares
keep basis 55.

**E2 — bands.** Net 2026 gain 100,000 DKK, threshold 79,400:
tax = 0.27 * 79,400 + 0.42 * 20,600 = 21,438 + 8,652 = 30,090 DKK.

**E3 — ring-fenced loss.** Year 1: net -40,000 DKK -> tax 0, carry 40,000.
Year 2: net +100,000 -> taxable 60,000 -> tax = 0.27 * 60,000 = 16,200 DKK
(2026 threshold). Carry exhausted.

**E4 — FX component.** Buy 100 @ 50 USD at USD/DKK 6.50 (basis 32,500 DKK).
Sell 100 @ 50 USD at USD/DKK 7.00 (proceeds 35,000 DKK). Share price flat, but
taxable gain = 2,500 DKK.

**E5 — loss never leaks.** Any year with net < 0 must produce tax = 0 and must
never reduce non-share income anywhere in the ledger.

## 3. Reporting requirements

The backtest and the live EOD cycle both maintain: per-year realized
aktieindkomst ledger, running tax accrual for the current year, carried-loss
balance. The UI History view and research REPORT show after-tax equity as the
headline number; pre-tax shown one line below, for diagnosis only.
