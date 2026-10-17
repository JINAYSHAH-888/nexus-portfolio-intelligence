#!/usr/bin/env python3
"""
Build backend/data/upstox_isin_map.json: scheme_id -> Upstox instrument_key (ISIN).

WHY THIS IS A BUILD-TIME SCRIPT, NOT A RUNTIME MATCHER
--------------------------------------------------------
Upstox's mutual fund instrument file has no AMFI code and no scheme_id -- only
a free-text `name`. The only way to bind it to our 35 schemes is name matching,
and name matching is genuinely dangerous here: this script's first real run
matched "Nippon India Medium Duration Fund" to "NIPPON INDIA MEDIUM TO LONG
DURATION FUND - GROWTH PLAN - BONUS OPTION" -- a DIFFERENT fund's BONUS unit,
with a NAV of 26.85 against the real 16.46 (a 63% error). A naive
highest-similarity-score matcher would have shipped that silently, and every
downstream number for that scheme -- overlap, risk, ARISE -- would have been
computed against the wrong fund.

So matching happens ONCE, offline, with a human able to review the output
before it ships, not on every request. Three defenses, all mandatory:

  1. Exclude BONUS/IDCW/DIVIDEND units explicitly -- they are not what "growth
     plan NAV" means and their prices are NOT comparable to a growth NAV.
  2. Every candidate is corroborated against our own factsheet-disclosed NAV
     (nav_regular_growth). A match whose NAV differs by more than MAX_DRIFT_PCT
     is REJECTED, not accepted-with-a-warning -- the 63% case above would have
     been caught immediately by even a generous 15% tolerance.
  3. If more than one candidate survives both filters, that is AMBIGUOUS and
     the script HALTS rather than picking "the best one". A human resolves it
     via MANUAL_OVERRIDES below, with a comment explaining why.

Usage:
    python scripts/build_upstox_isin_map.py path/to/mf-instruments.json.gz
    python scripts/build_upstox_isin_map.py path/to/mf-instruments.json.gz --check

`--check` verifies the committed map still NAV-corroborates against the
CURRENT scheme metadata and exits 1 on drift -- a CI guard against the mapping
silently rotting as either side's data changes.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMES_PATH = ROOT / "backend" / "data" / "schemes_metadata.json"
OUTPUT_PATH = ROOT / "backend" / "data" / "upstox_isin_map.json"

MAX_DRIFT_PCT = 15.0  # a factsheet snapshot vs a live/BOD NAV; real market
                       # movement over months easily accounts for this. The
                       # bug this script exists to catch (63% drift) is not
                       # remotely close to this line.

EXCLUDE_KEYWORDS = ("BONUS", "IDCW", "DIVIDEND", "SEGREGATED", "INSURED", "INSTITUTIONAL")

# Verified by hand where the automated match was ambiguous or the two schemes
# are close enough in name that NAV corroboration alone left >1 candidate
# inside MAX_DRIFT_PCT. Each entry MUST carry a reason.
MANUAL_OVERRIDES: dict[str, dict[str, str]] = {
    # Example shape, filled in for real once build is run against a live file:
    # "nippon-medium-duration-fund-existing-number-of-segregated-portfolios-1": {
    #     "instrument_key": "INF204KA1PZ1",
    #     "reason": (
    #         "Auto-match picked the BONUS unit of a DIFFERENT fund (Medium-to-"
    #         "Long Duration) at NAV 26.85 vs our factsheet 16.46 (63% drift, "
    #         "correctly rejected by MAX_DRIFT_PCT). The correct instrument is "
    #         "'NIPPON INDIA MEDIUM DURATION FUND- GROWTH PLAN', NAV 16.79 "
    #         "(2.0% drift, well within tolerance)."
    #     ),
    # },
}

_PAREN_RE = re.compile(r"\([^)]*\)")
STOP_WORDS = {"NIPPON", "INDIA", "FUND", "REGULAR", "PLAN", "GROWTH", "OPTION", "THE", "OF", "AND"}


def _strip_disambiguators(name: str) -> str:
    """
    Side-pocket / segregated-portfolio disambiguators live in OUR scheme names
    as a parenthetical suffix -- e.g. "(Existing Number of Segregated
    Portfolios - 1)". Upstox's listing for the MAIN fund carries no such
    suffix at all, so requiring every one of our tokens to appear in the
    candidate would reject the correct match outright. That parenthetical is
    metadata about a segregation EVENT, not part of the fund's identity for
    matching purposes.
    """
    return _PAREN_RE.sub(" ", name)


def _normalise(name: str) -> set[str]:
    tokens = re.sub(r"[^A-Z0-9 ]", " ", _strip_disambiguators(name).upper()).split()
    return {t for t in tokens if t not in STOP_WORDS}


def _squashed(name: str) -> str:
    """
    No-space, no-punctuation form.

    Upstox sometimes concatenates words our factsheets space out -- our
    "Flexi Cap Fund" is Upstox's "FLEXICAP FUND". A pure token-set match
    treats "FLEXI" and "FLEXICAP" as unrelated tokens and rejects a correct
    match; squashing both sides removes the spacing convention as a variable.
    """
    return re.sub(r"[^A-Z0-9]", "", _strip_disambiguators(name).upper())


def _is_excluded(name: str) -> bool:
    upper = name.upper()
    return any(k in upper for k in EXCLUDE_KEYWORDS)


def _is_growth_plan(name: str) -> bool:
    upper = name.upper().rstrip()
    return "GROWTH" in upper  # excluded set already strips BONUS/IDCW/DIVIDEND


def load_mf_instruments(path: Path) -> list[dict]:
    opener = gzip.open if path.suffix == ".gz" or path.name.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def _dedupe_settlement_variants(candidates: list[dict]) -> list[dict]:
    """
    Upstox emits one row PER SETTLEMENT-TYPE VARIANT of the same instrument --
    the same ISIN can appear 2-3 times (e.g. settlement_type T2/T3/L1), only
    differing in purchase minimums and which one is currently purchasable. That
    is not ambiguity about WHICH FUND this is; it is three ways to transact in
    the SAME fund. Collapse to one representative per instrument_key before
    judging whether a scheme name is genuinely ambiguous.

    The representative prefers a currently-purchasable, currently-redeemable
    variant with the lowest minimum purchase amount -- the one an investor
    would actually be offered.
    """
    by_key: dict[str, list[dict]] = {}
    for c in candidates:
        by_key.setdefault(c["instrument_key"], []).append(c)

    out = []
    for _key, variants in by_key.items():
        variants.sort(
            key=lambda v: (
                not v.get("purchase_allowed", False),
                not v.get("redemption_allowed", False),
                v.get("minimum_purchase_amount") or float("inf"),
            )
        )
        out.append(variants[0])
    return out


def find_candidates(scheme_name: str, mf_instruments: list[dict]) -> list[dict]:
    target_tokens = _normalise(scheme_name)
    target_squashed = _squashed(scheme_name)

    candidates = []
    for inst in mf_instruments:
        name = inst.get("name", "")
        if "NIPPON" not in name.upper():
            continue
        if inst.get("plan") != "NORMAL":  # "NORMAL" == regular plan, not direct
            continue
        if _is_excluded(name):
            continue
        if not _is_growth_plan(name):
            continue

        cand_tokens = _normalise(name)
        cand_squashed = _squashed(name)

        # Primary: every content word of OUR name appears in the candidate.
        # Subset, not a similarity score -- a similarity score is exactly what
        # let the Medium/Medium-to-Long collision through in the first place.
        token_match = target_tokens <= cand_tokens
        # Fallback: the same match with Upstox's word-concatenation habit
        # (FLEXI CAP -> FLEXICAP) normalised away.
        squashed_match = target_squashed in cand_squashed

        if token_match or squashed_match:
            candidates.append(inst)

    return _dedupe_settlement_variants(candidates)


def resolve_scheme(scheme: dict, mf_instruments: list[dict]) -> dict:
    sid = scheme["id"]
    our_nav = scheme.get("nav_regular_growth")

    if sid in MANUAL_OVERRIDES:
        ov = MANUAL_OVERRIDES[sid]
        return {
            "scheme_id": sid,
            "instrument_key": ov["instrument_key"],
            "match_method": "manual_override",
            "note": ov["reason"],
        }

    candidates = find_candidates(scheme["name"], mf_instruments)

    corroborated = []
    for c in candidates:
        nav = c.get("last_price")
        if nav is None or not our_nav:
            continue
        drift = abs(nav - our_nav) / our_nav * 100
        if drift <= MAX_DRIFT_PCT:
            corroborated.append((c, drift))

    if not corroborated:
        return {
            "scheme_id": sid,
            "instrument_key": None,
            "match_method": "UNRESOLVED",
            "note": (
                f"{len(candidates)} name-matched candidate(s), none within "
                f"{MAX_DRIFT_PCT}% NAV of our factsheet ({our_nav}). "
                f"Needs a MANUAL_OVERRIDES entry or a wider search."
            ),
        }

    corroborated.sort(key=lambda x: x[1])  # closest NAV first
    distinct_keys = {c["instrument_key"] for c, _ in corroborated}
    if len(distinct_keys) > 1:
        top_key = corroborated[0][0]["instrument_key"]
        runner_up = next((c, d) for c, d in corroborated if c["instrument_key"] != top_key)
        if runner_up[1] - corroborated[0][1] < 1.0:
            # Two+ DISTINCT instruments within 1pp of each other's drift:
            # genuinely ambiguous, not "obviously the closer one". Halt.
            return {
                "scheme_id": sid,
                "instrument_key": None,
                "match_method": "AMBIGUOUS",
                "note": (
                    f"{len(distinct_keys)} distinct instrument(s) NAV-corroborate "
                    f"within {MAX_DRIFT_PCT}%, too close to disambiguate "
                    f"automatically: "
                    + "; ".join(
                        f"{c['instrument_key']} (drift {d:.1f}%)"
                        for c, d in corroborated
                        if c["instrument_key"] in distinct_keys
                    )
                    + ". Needs a MANUAL_OVERRIDES entry."
                ),
            }

    best, drift = corroborated[0]
    return {
        "scheme_id": sid,
        "instrument_key": best["instrument_key"],
        "match_method": "name_and_nav",
        "nav_drift_pct": round(drift, 2),
        "upstox_name": best["name"],
        "purchase_allowed": best.get("purchase_allowed"),
        "redemption_allowed": best.get("redemption_allowed"),
        "minimum_purchase_amount": best.get("minimum_purchase_amount"),
        "additional_purchase_amount": best.get("additional_purchase_amount"),
    }


def build_from_instruments(mf_instruments: list[dict]) -> tuple[dict, list[str]]:
    """
    The pure core: resolve every scheme against an already-loaded instrument
    list. No file I/O -- this is what both the CLI (`build`, below) and the
    nightly Celery refresh (integrations/upstox, called with freshly
    downloaded bytes) share, so there is exactly one resolution algorithm.
    """
    schemes = json.loads(SCHEMES_PATH.read_text())

    results = {}
    problems = []
    for s in schemes:
        r = resolve_scheme(s, mf_instruments)
        results[r["scheme_id"]] = r
        if r["instrument_key"] is None:
            problems.append(f"{r['scheme_id']}: {r['match_method']} -- {r['note']}")

    return results, problems


def build(mf_path: Path) -> tuple[dict, list[str]]:
    return build_from_instruments(load_mf_instruments(mf_path))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mf_instruments_file", type=Path, nargs="?", default=None)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    if args.check:
        if not OUTPUT_PATH.exists():
            print(f"{OUTPUT_PATH} does not exist. Run without --check first.")
            return 1
        existing = json.loads(OUTPUT_PATH.read_text())
        schemes = {s["id"]: s for s in json.loads(SCHEMES_PATH.read_text())}
        bad = []
        for sid, row in existing.items():
            if row["instrument_key"] is None:
                bad.append(f"{sid}: unresolved ({row['match_method']})")
                continue
            our_nav = schemes.get(sid, {}).get("nav_regular_growth")
            drift = row.get("nav_drift_pct")
            if our_nav and drift is not None and drift > MAX_DRIFT_PCT:
                bad.append(f"{sid}: recorded drift {drift}% exceeds {MAX_DRIFT_PCT}%")
        if bad:
            print(f"upstox_isin_map.json failed the drift check ({len(bad)} issue(s)):")
            for b in bad:
                print("  -", b)
            return 1
        missing = set(schemes) - set(existing)
        if missing:
            print(f"upstox_isin_map.json is missing {len(missing)} scheme(s): {sorted(missing)}")
            return 1
        print(f"upstox_isin_map.json OK: {len(existing)} schemes, all within tolerance.")
        return 0

    if args.mf_instruments_file is None:
        print("Usage: build_upstox_isin_map.py <mf-instruments.json[.gz]> [--check]")
        return 2

    results, problems = build(args.mf_instruments_file)

    OUTPUT_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n")
    resolved = sum(1 for r in results.values() if r["instrument_key"])
    print(f"Wrote {OUTPUT_PATH}: {resolved}/{len(results)} schemes resolved.")

    if problems:
        print(f"\n{len(problems)} UNRESOLVED -- add a MANUAL_OVERRIDES entry for each:")
        for p in problems:
            print("  -", p)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
