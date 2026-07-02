# Bundled datasets - provenance and limitations

## sp500_ticker_start_end.csv

Point-in-time S&P 500 membership as `ticker,start_date,end_date` intervals. An
empty `end_date` means the ticker is still a current member. A ticker may appear
on multiple rows (removed and later re-added), so membership is the union of its
intervals.

- **Source:** [fja05680/sp500](https://github.com/fja05680/sp500),
  file `sp500_ticker_start_end.csv`.
- **License:** MIT, Copyright (c) 2019-2020 Farrell J. Aultman. See
  `sp500_source_LICENSE.txt` in this folder.
- **Ultimate source:** compiled from Wikipedia's S&P 500 constituents and change
  history.
- **Refresh:** `python scripts/fetch_universe.py` re-downloads the current file.

### Known limitations (read before trusting a backtest)

1. **Membership only, not survivorship-free prices.** This gives *which* symbols
   were in the index on a date. Price data still comes from the bar source and
   may be missing for delisted/renamed tickers, which is its own bias.
2. **Ticker changes / corporate actions.** Symbols reflect the ticker in use;
   renames, mergers and share-class quirks are not fully reconciled. Some old
   tickers (e.g. `AABA`, `AAMRQ`) will have no usable price history.
3. **Wikipedia-derived.** Dates are as accurate as the underlying edit history;
   treat boundary dates as approximate (+/- a few days).
4. **Not a legal index membership record.** For research use only.

The research report (Phase 3) must restate these limitations when presenting
results, per docs/PLAN.md.
