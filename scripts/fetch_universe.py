"""Refresh the bundled S&P 500 membership dataset from its public source.

Downloads sp500_ticker_start_end.csv from fja05680/sp500 (MIT license) into
tradehelm/data/datasets/. Network; run manually. See datasets/DATA_PROVENANCE.md.

If your machine intercepts TLS (corporate proxy) and this fails with a
certificate error, download the file in a browser and drop it in place instead.
"""

from __future__ import annotations

import ssl
import urllib.request

from tradehelm.data import Universe

SOURCE_URL = "https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv"


def main() -> int:
    dest = Universe.default_dataset_path()
    context = ssl.create_default_context()
    with urllib.request.urlopen(SOURCE_URL, context=context, timeout=60) as resp:  # noqa: S310
        data = resp.read()
    dest.write_bytes(data)
    universe = Universe.from_csv(dest)  # validate it parses
    print(f"wrote {dest} ({len(data)} bytes, {len(universe.all_symbols())} tickers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
