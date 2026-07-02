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

    # Validate into a temp file first; only replace the bundled dataset once it
    # parses, so a malformed/schema-changed download can't corrupt the committed
    # file and break Universe.default() / data pulls.
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    try:
        universe = Universe.from_csv(tmp)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
    print(f"wrote {dest} ({len(data)} bytes, {len(universe.all_symbols())} tickers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
