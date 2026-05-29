from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Any, List

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors.okx_public import OkxPublicClient, merge_rows


DEFAULT_OUT = ROOT / "data" / "okx_public_ohlcv.csv"
DEFAULT_SYMBOLS = ["BTC-USDT-SWAP", "ETH-USDT-SWAP", "SOL-USDT-SWAP"]
FIELDS = ["timestamp", "symbol", "timeframe", "open", "high", "low", "close", "volume", "funding", "oi"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch OKX public market rows for Phase -1 replay CSV.")
    parser.add_argument("--symbol", action="append", dest="symbols", help="OKX instId. Repeatable.")
    parser.add_argument("--bar", default="1H", choices=["15m", "1H", "4H"])
    parser.add_argument("--limit", type=int, default=100, help="Rows per OKX page, capped at 100.")
    parser.add_argument("--pages", type=int, default=1, help="Number of public history pages to fetch per symbol.")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    client = OkxPublicClient()
    symbols = args.symbols or DEFAULT_SYMBOLS
    rows: List[dict[str, Any]] = []
    for symbol in symbols:
        candles = client.history_candles_pages(symbol, bar=args.bar, limit=args.limit, pages=args.pages)
        funding_pages = max(1, min(args.pages, 10))
        funding = client.funding_rate_history_pages(symbol, limit=min(args.limit, 100), pages=funding_pages)
        oi = client.open_interest(symbol)
        rows.extend(merge_rows(candles, funding, oi))

    rows.sort(key=lambda item: (item["symbol"], item["timestamp"]))
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} public OKX rows to {out}")
    print("private_api=not_used")


if __name__ == "__main__":
    main()
