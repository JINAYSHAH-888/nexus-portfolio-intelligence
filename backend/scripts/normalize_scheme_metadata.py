#!/usr/bin/env python3
"""
Normalise `backend/data/schemes_metadata.json` in place, ADDITIVELY.

WHY THIS EXISTS (a real bug, found by reading the code against the data)
-----------------------------------------------------------------------
`analytics/debt_returns_model.py` -- the Tier-3 fallback that CLAUDE.md §3
describes as using "each debt scheme's own real modified_duration_years +
ytm_decimal, NOT a generic category average" -- reads exactly these keys:

    _anchor_mean(meta) -> meta["trailing_returns"][...]  or meta["ytm_decimal"]
    _anchor_vol(meta)  -> meta["modified_duration_years"]

But the factsheet-extracted data actually carries:

    "modified_duration": "10.22 Years"   (a STRING, different key)
    "ytm":               "7.67%"         (a STRING, different key)

So `meta.get("modified_duration_years")` was None for all 15 debt schemes, and
`_anchor_vol` fell through to `CREDIT_LIQUIDITY_VOL_FLOOR` (0.002 = 20 bps)
for every single one of them. Concretely: the Nivesh Lakshya long-duration
fund (10.22y duration -> ~12.3% annual vol) was being modelled as *exactly as
volatile as the overnight fund* (1-day duration). That silently understates
debt risk everywhere downstream -- the risk dashboard's VaR/CVaR, the
covariance matrix, and ARISE's ruin probability for any debt-heavy portfolio.

`_anchor_mean` was fine by luck: all 15 debt schemes do carry `trailing_returns`
with a `3_years` entry, which it prefers over `ytm_decimal`.

THE FIX
-------
Do NOT edit debt_returns_model.py -- its logic is correct and its documented
contract is the sensible one. Instead, give it the fields it asks for.

This script parses the real factsheet strings into the numeric keys the engine
documents, and writes them back as NEW keys. The original strings are left
byte-for-byte intact, so nothing that reads `modified_duration`/`ytm` today
changes behaviour, and the factsheet provenance is preserved.

Idempotent: re-running recomputes the same values. Safe in CI.

    python scripts/normalize_scheme_metadata.py [--check]

`--check` exits 1 if the file would change (use it as a CI guard).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMES = ROOT / "backend" / "data" / "schemes_metadata.json"

TRADING_DAYS_PER_YEAR = 365.0  # calendar basis: "45 Days" is calendar, not trading

_DURATION_RE = re.compile(r"^\s*([\d.]+)\s*(Days?|Years?|Months?)\s*$", re.IGNORECASE)
_PCT_RE = re.compile(r"^\s*([\d.]+)\s*%?\s*$")


def parse_duration_years(raw: object) -> float | None:
    """
    "1 Days"      -> 0.00274
    "45 Days"     -> 0.1233
    "10.22 Years" -> 10.22
    "6 Months"    -> 0.5
    """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    m = _DURATION_RE.match(str(raw))
    if not m:
        return None
    value, unit = float(m.group(1)), m.group(2).lower()
    if unit.startswith("day"):
        return value / TRADING_DAYS_PER_YEAR
    if unit.startswith("month"):
        return value / 12.0
    return value


def parse_pct_decimal(raw: object) -> float | None:
    """ "7.90%" -> 0.079 ; 7.9 -> 0.079 ; 0.079 -> 0.079 """
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        v = float(raw)
        # A YTM given as a bare number is a percentage (7.9), unless it is
        # already a small decimal (0.079).
        return v / 100.0 if v > 1.0 else v
    m = _PCT_RE.match(str(raw))
    if not m:
        return None
    return float(m.group(1)) / 100.0


def _put(
    scheme: dict,
    sid: str,
    changes: list[str],
    key: str,
    value: float | None,
    *,
    log: bool = False,
) -> None:
    """
    Write a derived key, rounding BEFORE comparing.

    Comparing a freshly-parsed float against the already-rounded value on disk
    would report a change on every run and break idempotence -- which
    test_normalisation_is_idempotent pins down.
    """
    if value is None:
        return
    rounded = round(value, 6)
    if scheme.get(key) != rounded:
        scheme[key] = rounded
        if log:
            changes.append(f"{sid}: {key} = {rounded:.4f}")


def normalise(schemes: list[dict]) -> tuple[list[dict], list[str]]:
    changes: list[str] = []
    for s in schemes:
        sid = s.get("id", "?")

        # Round BEFORE comparing. Comparing a freshly-parsed float against the
        # rounded value already on disk would report a change on every run and
        # break idempotence (caught by test_normalisation_is_idempotent).
        _put(s, sid, changes, "modified_duration_years",
             parse_duration_years(s.get("modified_duration")), log=True)
        _put(s, sid, changes, "macaulay_duration_years",
             parse_duration_years(s.get("macaulay_duration")))
        _put(s, sid, changes, "avg_maturity_years",
             parse_duration_years(s.get("avg_maturity")))
        _put(s, sid, changes, "ytm_decimal",
             parse_pct_decimal(s.get("ytm")), log=True)

    return schemes, changes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="exit 1 if changes are needed")
    args = ap.parse_args()

    original = SCHEMES.read_text(encoding="utf-8")
    schemes = json.loads(original)
    schemes, changes = normalise(schemes)
    updated = json.dumps(schemes, indent=2, ensure_ascii=False) + "\n"

    if args.check:
        if updated != original:
            print(f"schemes_metadata.json is NOT normalised ({len(changes)} changes pending):")
            for c in changes[:10]:
                print("  -", c)
            return 1
        print("schemes_metadata.json is normalised.")
        return 0

    SCHEMES.write_text(updated, encoding="utf-8")
    print(f"Normalised {len(schemes)} schemes; {len(changes)} field(s) written.")

    debt = [s for s in schemes if s.get("instrument_type") == "debt"]
    missing = [s["id"] for s in debt if s.get("modified_duration_years") is None]
    print(f"Debt schemes with modified_duration_years: {len(debt) - len(missing)}/{len(debt)}")
    if missing:
        print("  STILL MISSING (would fall back to the 20bps vol floor):", missing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
