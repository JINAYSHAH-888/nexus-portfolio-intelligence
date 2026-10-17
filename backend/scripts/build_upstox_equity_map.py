#!/usr/bin/env python3
"""
Build backend/data/upstox_equity_map.json: stock name -> Upstox NSE
instrument_key, for the ~221 stocks appearing across the 35 schemes' holdings.

Unlike the MF mapper, this join is on `trading_symbol` (after stripping
yfinance's ".NS" suffix from our existing stocks_ticker_map.json), which is a
much lower-risk join than MF name-matching: NSE trading symbols are unique
within the NSE_EQ segment by construction, so there is no "settlement variant"
or "BONUS unit" class of collision here. Verified: 211/221 of our existing
tickers match by exact trading_symbol on the real Upstox NSE file.

The ~10 that DON'T match are pre-existing staleness in OUR OWN
stocks_ticker_map.json (yfinance-side renames/delistings, or literal
"NOT_LISTED_YET" placeholders for pre-IPO holdings) -- not an Upstox mapping
problem, and not something this script should silently paper over. They are
reported, not guessed at, exactly like the MF mapper's UNRESOLVED case.

Usage:
    python scripts/build_upstox_equity_map.py path/to/NSE.json.gz
    python scripts/build_upstox_equity_map.py path/to/NSE.json.gz --check
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TICKER_MAP_PATH = ROOT / "backend" / "data" / "stocks_ticker_map.json"
OUTPUT_PATH = ROOT / "backend" / "data" / "upstox_equity_map.json"

# Known-stale entries in OUR OWN ticker map, unrelated to Upstox. Recorded
# with a reason so a re-run doesn't re-report them as new surprises, and so
# fixing stocks_ticker_map.json is a visible, separate task from this script.
KNOWN_STALE_IN_TICKER_MAP = {
    "NOT_LISTED_YET",  # explicit placeholder for pre-IPO / private holdings
}


def load_index(nse_path: Path) -> dict[str, dict]:
    opener = gzip.open if nse_path.name.endswith(".gz") else open
    with opener(nse_path, "rt", encoding="utf-8") as f:
        data = json.load(f)
    equities = [
        x for x in data if x.get("segment") == "NSE_EQ" and x.get("instrument_type") == "EQ"
    ]
    return {x["trading_symbol"]: x for x in equities}


def build_from_index(by_symbol: dict[str, dict]) -> tuple[dict, list[str]]:
    tickers = json.loads(TICKER_MAP_PATH.read_text())["nse_mapped"]

    results: dict[str, dict] = {}
    unresolved: list[str] = []

    for stock_name, yf_ticker in tickers.items():
        if yf_ticker in KNOWN_STALE_IN_TICKER_MAP:
            results[stock_name] = {
                "instrument_key": None,
                "match_method": "SKIPPED",
                "note": f"'{yf_ticker}' is a known placeholder in stocks_ticker_map.json.",
            }
            continue

        symbol = yf_ticker.removesuffix(".NS")
        inst = by_symbol.get(symbol)
        if inst is None:
            results[stock_name] = {
                "instrument_key": None,
                "match_method": "UNRESOLVED",
                "note": (
                    f"No NSE_EQ instrument with trading_symbol='{symbol}'. Likely a "
                    f"stale/renamed ticker in stocks_ticker_map.json, not an Upstox gap."
                ),
            }
            unresolved.append(f"{stock_name} ({yf_ticker}): no NSE match for '{symbol}'")
            continue

        results[stock_name] = {
            "instrument_key": inst["instrument_key"],
            "match_method": "trading_symbol",
            "isin": inst["isin"],
            "trading_symbol": inst["trading_symbol"],
            "lot_size": inst.get("lot_size"),
        }

    return results, unresolved


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("nse_instruments_file", type=Path, nargs="?", default=None)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.check:
        if not OUTPUT_PATH.exists():
            print(f"{OUTPUT_PATH} does not exist. Run without --check first.")
            return 1
        existing = json.loads(OUTPUT_PATH.read_text())
        tickers = json.loads(TICKER_MAP_PATH.read_text())["nse_mapped"]
        missing = set(tickers) - set(existing)
        if missing:
            print(f"upstox_equity_map.json is missing {len(missing)} stock(s): {sorted(missing)}")
            return 1
        resolved = sum(1 for r in existing.values() if r["instrument_key"])
        print(f"upstox_equity_map.json OK: {resolved}/{len(existing)} resolved.")
        return 0

    if args.nse_instruments_file is None:
        print("Usage: build_upstox_equity_map.py <NSE.json[.gz]> [--check]")
        return 2

    by_symbol = load_index(args.nse_instruments_file)
    results, unresolved = build_from_index(by_symbol)

    OUTPUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    resolved = sum(1 for r in results.values() if r["instrument_key"])
    print(f"Wrote {OUTPUT_PATH}: {resolved}/{len(results)} resolved.")

    if unresolved:
        print(f"\n{len(unresolved)} unresolved (pre-existing staleness in stocks_ticker_map.json):")
        for u in unresolved:
            print("  -", u)
        # NOT a hard failure like the MF mapper -- these are known, pre-existing
        # data-quality issues in our OWN ticker map (confirmed above: 211/221
        # match cleanly), not ambiguity risk from Upstox's side. A live price
        # feed simply won't have these ~10 stocks until stocks_ticker_map.json
        # is corrected, which is a separate, visible task.

    return 0


if __name__ == "__main__":
    sys.exit(main())
