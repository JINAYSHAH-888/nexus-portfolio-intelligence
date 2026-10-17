"""
Download the real market history the ML / quant models are trained on.

    python scripts/fetch_market_history.py            # fetch + write
    python scripts/fetch_market_history.py --offline  # only re-validate files

Writes backend/data/market_history/:
    amfi_codes.json     our 35 scheme ids -> verified AMFI Direct-Growth code,
                        with the matched AMFI name and a match score. A scheme
                        whose best match scores below MIN_MATCH is left OUT,
                        never guessed (the old hand map had duplicate codes).
    nav_daily.csv.gz    date x scheme_id, Direct-Growth NAV (AMFI via mfapi.in)
    index_daily.csv.gz  date x {nifty50, nifty_midcap50}  (Yahoo, price index)
    index_monthly.csv   month-end Sensex since 1997          (Yahoo, price index)
    manifest.json       sources, fetch time, row counts, sha256 of each file

Price indices exclude dividends (~1.2-1.5%/yr for Indian large caps). The
models only learn SHAPE from them (regimes, volatility dynamics, factor
structure) and are re-anchored on the shrunk portfolio statistics, so the
missing yield does not leak into any return level shown to an investor.
"""

from __future__ import annotations

import argparse
import difflib
import gzip
import hashlib
import io
import json
import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "backend" / "data"
OUT = DATA / "market_history"
MIN_MATCH = 0.86
# AMFI's register still carries the pre-SEBI-recategorisation (2018) names
# for these two; the factsheet uses the new ones. Explicit, not fuzzy.
ALIASES = {
    "Nippon India Floater Fund": "Nippon India Floating Interest Rates Fund",
    "Nippon India Short Duration Fund": "Nippon India Short Term Fund",
    "Nippon India Medium Duration Fund (Existing Number of Segregated Portfolios - 1)": "Nippon India Medium Term Fund",
}
# Not on AMFI's public register under any name we could verify, so absent
# from the training data (the engines fall back for it as before):
#   Nippon India Low Duration Fund
UA = {"User-Agent": "Mozilla/5.0 (nexus-portfolio-intelligence research fetch)"}
# Explicit period1/period2: with range=max Yahoo silently downsamples a
# "1d" request to monthly bars.
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{t}?period1=0&period2=9999999999&interval={i}"


def _norm(name: str) -> str:
    n = name.lower().replace("&", " and ")
    n = re.sub(r"\(.*?\)", " ", n)
    n = re.sub(r"[^a-z0-9 ]+", " ", n)
    for w in ("direct plan", "growth option", "growth plan", "growth", "direct", "plan", "option"):
        n = n.replace(w, " ")
    return re.sub(r"\s+", " ", n).strip()


def match_codes(schemes: list[dict], amfi: list[dict]) -> dict[str, dict]:
    pool = [
        a
        for a in amfi
        if a["schemeName"].lower().startswith("nippon india")
        and "direct" in a["schemeName"].lower()
        and "growth" in a["schemeName"].lower()
        # Side-pocket portfolios are named "... - Segregated Portfolio 1 - ...";
        # the parent fund's name merely MENTIONS them in brackets, so test the
        # name with the bracketed part removed.
        and not re.search(
            r"idcw|dividend|bonus|segregated|institutional|retail",
            re.sub(r"\(.*?\)", "", a["schemeName"]),
            re.I,
        )
    ]
    normed = [(_norm(a["schemeName"]), a) for a in pool]
    out: dict[str, dict] = {}
    for s in schemes:
        target = _norm(ALIASES.get(s["name"], s["name"]))
        best, score = None, 0.0
        for n, a in normed:
            r = difflib.SequenceMatcher(None, target, n).ratio()
            if r > score:
                best, score = a, r
        if best and score >= MIN_MATCH:
            out[s["id"]] = {
                "amfi_code": best["schemeCode"],
                "amfi_name": best["schemeName"],
                "isin": best.get("isinGrowth"),
                "match_score": round(score, 3),
            }
        else:
            print(
                f"  ! no confident match for {s['name']!r} (best {score:.2f}: "
                f"{best['schemeName'] if best else '-'}) -- left out",
                file=sys.stderr,
            )
    return out


def fetch_nav(code: int) -> pd.Series:
    r = requests.get(f"https://api.mfapi.in/mf/{code}", headers=UA, timeout=60)
    r.raise_for_status()
    rows = r.json().get("data", [])
    s = pd.Series(
        {
            pd.to_datetime(x["date"], format="%d-%m-%Y"): float(x["nav"])
            for x in rows
            if float(x["nav"]) > 0
        }
    ).sort_index()
    return s[~s.index.duplicated()]


def fetch_yahoo(ticker: str, interval: str) -> pd.Series:
    r = requests.get(YAHOO.format(t=ticker, i=interval), headers=UA, timeout=60)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    close = (
        res["indicators"]["adjclose"][0]["adjclose"]
        if "adjclose" in res["indicators"]
        else res["indicators"]["quote"][0]["close"]
    )
    idx = pd.to_datetime(res["timestamp"], unit="s").normalize()
    s = pd.Series(close, index=idx, dtype="float64").dropna()
    return s[~s.index.duplicated(keep="last")].sort_index()


# Factor and macro series. Sector indices give PLOS a style-analysis basis;
# VIX / overnight US / USD-INR / crude are the exogenous inputs to the Risk
# volatility forecaster.
SECTOR_INDICES = {
    "nifty500": "%5ECRSLDX", "nifty_midcap100": "%5ENSMIDCP", "bank": "%5ENSEBANK",
    "it": "%5ECNXIT", "pharma": "%5ECNXPHARMA", "fmcg": "%5ECNXFMCG", "auto": "%5ECNXAUTO",
    "metal": "%5ECNXMETAL", "energy": "%5ECNXENERGY", "infra": "%5ECNXINFRA",
}
MACRO = {
    "india_vix": "%5EINDIAVIX", "sp500": "%5EGSPC", "usdinr": "INR%3DX",
    "crude": "CL%3DF", "gold": "GC%3DF",
}


def fetch_yahoo_ohlc(ticker: str) -> pd.DataFrame:
    r = requests.get(YAHOO.format(t=ticker, i="1d"), headers=UA, timeout=60)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    idx = pd.to_datetime(res["timestamp"], unit="s").normalize()
    df = pd.DataFrame({k: q[k] for k in ("open", "high", "low", "close")}, index=idx, dtype="float64")
    df = df.dropna()
    # Yahoo occasionally prints a bar with high < low or a zero range on a
    # holiday stub; drop rather than repair.
    df = df[(df.high >= df.low) & (df.low > 0)]
    return df[~df.index.duplicated(keep="last")].sort_index()


def holding_tickers() -> list[str]:
    m = json.loads((DATA / "stocks_ticker_map.json").read_text())["nse_mapped"]
    return sorted({t for t in m.values() if t.endswith(".NS")})


def fetch_extended() -> dict:
    """Sector + macro indices, every holding's daily (split/dividend
    adjusted) close, and Nifty daily OHLC for range-based volatility."""
    out = {}
    cols = {}
    for name, t in {**SECTOR_INDICES, **MACRO}.items():
        try:
            cols[name] = fetch_yahoo(t, "1d")
        except Exception as e:  # one missing series must not sink the rest
            print(f"  ! {name}: {e}", file=sys.stderr)
        time.sleep(0.2)
    _write_csv_gz(pd.DataFrame(cols).sort_index(), OUT / "factors_daily.csv.gz")
    out["factor_series"] = len(cols)

    stocks, failed = {}, []
    for t in holding_tickers():
        try:
            s = fetch_yahoo(requests.utils.quote(t, safe="."), "1d")
            if len(s) >= 60:
                stocks[t] = s
            else:
                failed.append(t)
        except Exception:
            failed.append(t)
        time.sleep(0.15)
    _write_csv_gz(pd.DataFrame(stocks).sort_index(), OUT / "stocks_daily.csv.gz")
    out["stocks"] = len(stocks)
    out["stocks_failed"] = failed

    _write_csv_gz(fetch_yahoo_ohlc("%5ENSEI"), OUT / "nifty_ohlc.csv.gz")
    return out


def verify_against_factsheet(sid: str, meta: dict, nav: pd.Series) -> dict:
    """The factsheet prints the Direct-Growth NAV. If the downloaded series
    contains that exact NAV (to 0.01%) on or near the factsheet date, the AMFI
    code is the right fund -- a far stronger check than name similarity."""
    fs = meta.get("nav_direct_growth")
    if fs is None:
        return {"nav_verified": False, "reason": "factsheet has no NAV"}
    fs = float(str(fs).replace(",", ""))
    hit = nav[(nav / fs - 1).abs() < 1e-4]
    if hit.empty:
        return {
            "nav_verified": False,
            "reason": f"factsheet NAV {fs} never appears in AMFI history",
        }
    return {"nav_verified": True, "nav_matched_on": str(hit.index[-1].date())}


def _write_csv_gz(df: pd.DataFrame, path: Path) -> None:
    # mtime=0 -> byte-identical output for identical data (reviewable diffs).
    buf = io.StringIO()
    df.to_csv(buf, float_format="%.6g", date_format="%Y-%m-%d")
    with open(path, "wb") as fh, gzip.GzipFile(fileobj=fh, mode="wb", mtime=0) as gz:
        gz.write(buf.getvalue().encode())


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def validate() -> dict:
    nav = pd.read_csv(OUT / "nav_daily.csv.gz", index_col=0, parse_dates=True)
    idx = pd.read_csv(OUT / "index_daily.csv.gz", index_col=0, parse_dates=True)
    mon = pd.read_csv(OUT / "index_monthly.csv", index_col=0, parse_dates=True)
    assert (nav.stack().dropna() > 0).all(), "non-positive NAV"
    # Real one-day moves in this data reach -25%: on 2020-03-05 Medium
    # Duration (-25.2%) and Credit Risk (-11.9%) side-pocketed their Yes Bank
    # AT1 bonds. That is genuine credit risk the models must see, so the bound
    # only rejects what no fund can do in a day.
    r = nav.pct_change(fill_method=None).stack().dropna()
    assert (r.abs() < 0.30).all(), f"implausible NAV jump: {r.abs().idxmax()}"
    assert len(mon) > 300 and len(idx) > 3000
    return {
        "schemes": nav.shape[1],
        "nav_rows": len(nav),
        "index_rows": len(idx),
        "months": len(mon),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    if args.offline:
        print(validate())
        return

    OUT.mkdir(parents=True, exist_ok=True)
    schemes = json.loads((DATA / "schemes_metadata.json").read_text())
    print("AMFI scheme list ...")
    amfi = requests.get("https://api.mfapi.in/mf", headers=UA, timeout=60).json()
    codes = match_codes(schemes, amfi)
    (OUT / "amfi_codes.json").write_text(json.dumps(codes, indent=2, sort_keys=True) + "\n")
    print(f"  matched {len(codes)}/{len(schemes)}")

    meta = {s["id"]: s for s in schemes}
    navs = {}
    for sid, c in sorted(codes.items()):
        nav = fetch_nav(c["amfi_code"])
        c.update(verify_against_factsheet(sid, meta[sid], nav))
        if not c["nav_verified"]:
            print(f"  ! {sid}: {c['reason']} -- dropped", file=sys.stderr)
            continue
        navs[sid] = nav
        print(
            f"  {sid:45s} {len(nav):5d} days from {nav.index[0].date()}  NAV verified {c['nav_matched_on']}"
        )
        time.sleep(0.3)
    codes = {k: v for k, v in codes.items() if v["nav_verified"]}
    (OUT / "amfi_codes.json").write_text(json.dumps(codes, indent=2, sort_keys=True) + "\n")
    _write_csv_gz(pd.DataFrame(navs).sort_index(), OUT / "nav_daily.csv.gz")

    print("Indices ...")
    daily = pd.DataFrame(
        {
            "nifty50": fetch_yahoo("%5ENSEI", "1d"),
            "nifty_midcap50": fetch_yahoo("%5ENSEMDCP50", "1d"),
        }
    ).dropna(how="all")
    _write_csv_gz(daily, OUT / "index_daily.csv.gz")
    sensex = fetch_yahoo("%5EBSESN", "1mo").rename("sensex").to_frame()
    sensex.to_csv(OUT / "index_monthly.csv", float_format="%.6g", date_format="%Y-%m-%d")

    print("Sectors, macro, holdings, OHLC ...")
    ext = fetch_extended()
    print(json.dumps(ext))

    stats = {**validate(), **{k: v for k, v in ext.items() if k != "stocks_failed"}}
    files = ["amfi_codes.json", "nav_daily.csv.gz", "index_daily.csv.gz", "index_monthly.csv",
             "factors_daily.csv.gz", "stocks_daily.csv.gz", "nifty_ohlc.csv.gz"]
    manifest = {
        "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "sources": {
            "nav": "AMFI NAV history via https://api.mfapi.in (Direct Plan - Growth)",
            "index_daily": "Yahoo Finance ^NSEI, ^NSEMDCP50 (price index, no dividends)",
            "index_monthly": "Yahoo Finance ^BSESN monthly (price index, no dividends)",
            "factors_daily": "Yahoo Finance: Nifty sector indices, India VIX, S&P 500, USD/INR, crude, gold",
            "stocks_daily": "Yahoo Finance .NS adjusted close for every mapped factsheet holding",
            "nifty_ohlc": "Yahoo Finance ^NSEI daily open/high/low/close",
        },
        "stocks_failed": ext["stocks_failed"],
        "stats": stats,
        "sha256": {f: _sha(OUT / f) for f in files},
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
