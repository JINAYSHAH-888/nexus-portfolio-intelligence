# Data sources

## Factsheet data (checked in)

`backend/backend/data/`:

| File | Contents |
|---|---|
| `schemes_metadata.json` | 35 Nippon India schemes (20 equity, 15 debt): category, AUM, riskometer, std dev, returns, duration, YTM |
| `sample_holdings.json` | Holdings per scheme as of 2026-05-31 — equity: stock, sector, weight; debt: issuer, rating, weight |
| `stocks_ticker_map.json` | 221 company-name → NSE ticker mappings |
| `historical_cpi_india.json` | Published India CPI, for ARISE inflation |

After re-importing factsheets, run `python scripts/normalize_scheme_metadata.py` — it derives the numeric `modified_duration_years` / `ytm_decimal` keys the debt model reads. CI runs it with `--check`.

## Market data fallback chain

`data_loaders/market_data.py::get_scheme_returns_with_fallback` tries, in order:

0. **Verified NAV history on disk** (`data/market_history/nav_daily.csv.gz`, AMFI codes verified against each factsheet's NAV), while under 30 days old
1. **AMFI NAV history** (mfapi.in, no key) — verified codes first, then the legacy map, name-validated against cross-fund contamination
2. **Holdings reconstruction** from stock prices (Upstox when connected, else yfinance) — equity only
3. **Debt analytical model** from each scheme's own duration and YTM
4. **Synthetic regime-switching** — last resort, shrunk toward a real prior; counts as zero years of evidence in ARISE

Fundamentals: Alpha Vantage (optional key) → yfinance. Live quotes: Upstox feed when connected, else delayed yfinance.

## Generated frontend data

Never hand-edited; regenerated and diff-checked in CI.

| Generator | Output | Gate |
|---|---|---|
| `frontend/scripts/build-overlap-dataset.mjs` | `public/data/equity-holdings.json`, `lib/generated/landing-facts.json` | 10 checks reproduce the engine's numbers |
| `frontend/scripts/build-tokens.mjs` | design tokens CSS | 16 WCAG AA contrast checks |

Run both with `make data tokens`.
