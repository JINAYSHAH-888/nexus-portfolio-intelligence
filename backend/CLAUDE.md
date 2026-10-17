# CLAUDE.md — Nippon India Overlap Platform (Backend)

This is the **real, developed backend** — FastAPI + Postgres + Redis + Celery
— NOT the Flask demo/prototype project built earlier in this project's
history. If you're picking this up in a new chat: this file is written so
you don't need anything else externally supplied. Read it fully before
touching code.

---

## 1. What This Project Is

An institutional-grade mutual fund overlap/risk/retirement intelligence
platform for Nippon India, built on **35 real schemes** (20 equity + 15 debt)
extracted from actual Nippon India factsheets (June 2026 data). Every number
in this system traces back to either real disclosed factsheet data, a real
market data API (yfinance/AMFI/Alpha Vantage), or a clearly-labeled,
documented estimate — nothing is fabricated demo data.

**The product flow** (fully wired end-to-end, verified via real HTTP
requests against an in-memory test DB — see §7):

```
Landing -> Register/Login -> Deep Profile (Phase 1)
  -> Filter Engine (Phase 2, informed-consent disclosure)
  -> Explore eligible schemes (heatmap/network via /analysis/overlap)
  -> Select schemes -> Allocate weights
  -> PLOS ranking (/analysis/plos)
  -> Risk dashboard (/analysis/risk)
  -> ARISE v4 retirement projection (/analysis/arise)
```

---

## 2. Architecture

```
backend/
|-- main.py                 FastAPI app entrypoint
|-- api/v1/                 REST routes (see Section 6 for full surface)
|   |-- auth.py              register/login/refresh/logout/me
|   |-- profile.py           Deep Profile CRUD (Phase 1 gate)
|   |-- schemes.py            /filtered /select /allocate /selected
|   |-- analysis.py          /overlap /plos /risk /arise /prices
|   |-- market.py            live prices, fundamentals, NAV, Celery task triggers
|   |-- reports.py           PDF report generation
|   |-- notifications.py     RM notifications
|   `-- websocket.py         /ws/live-prices
|-- engines/                 Pure-Python, DB-independent, unit-testable
|   |-- filter_engine.py      Deep-profile eligibility (Section 4 -- just redesigned)
|   |-- overlap_engine.py      Jaccard overlap, EEI, ORC, HHI
|   |-- plos_engine.py         10-layer Portfolio Level Overlap Score
|   |-- risk_engine.py         Ledoit-Wolf covariance, VaR/CVaR, Monte Carlo
|   `-- arise_engine.py        ARISE v4 retirement engine (Section 5 -- the real one)
|-- data_loaders/             External market data with graceful fallback
|   |-- market_data.py          orchestrates the fallback chain (Section 3)
|   |-- amfi_loader.py          AMFI NAV history (free, no key)
|   |-- yfinance_pipeline.py    stock price history (free, no key)
|   |-- alpha_vantage_loader.py fundamentals (optional key)
|   |-- live_price_loader.py    delayed live quotes (yfinance-based)
|   `-- amc_pdf_pipeline.py     factsheet PDF parsing (local files only, not live)
|-- analytics/
|   `-- portfolio_statistics.py builds real PortfolioStatistics for ARISE (shrinkage/cap fix lives here)
|-- services/                 Business logic layer, calls engines + DB
|-- models/                   SQLAlchemy ORM models
|-- schemas/                  Pydantic request/response models
|-- repositories/              DB access layer
|-- data/
|   |-- schemes_metadata.json   THE 35 real schemes (20 equity + 15 debt)
|   |-- sample_holdings.json    Real holdings per scheme (equity: stock+sector+weight; debt: issuer+rating+weight)
|   |-- stocks_ticker_map.json  221 real company-name -> NSE ticker mappings
|   `-- historical_cpi_india.json  Real published India CPI, for ARISE inflation modelling
`-- tasks/                     Celery background jobs (price/NAV refresh)

tests/                        241 tests, all passing (Section 7)
```

---

## 3. Data Source Architecture (yfinance / AMFI / Alpha Vantage)

| Purpose | Source | Key needed? |
|---|---|---|
| Historical prices (analytics) | yfinance + parquet cache | No |
| Delayed live quotes (display) | yfinance (`live_price_loader.py`) | No |
| MF NAV history | AMFI via mfapi.in (`amfi_loader.py`) | No |
| Fundamentals | Alpha Vantage -> yfinance fallback | Optional |
| Scheme returns (analytics fallback chain) | AMFI NAV -> yfinance holdings -> debt duration/YTM model -> synthetic | No |

**The fallback chain** (`market_data.py::get_scheme_returns_with_fallback`):
1. AMFI NAV history (name-validated to catch cross-fund contamination bugs)
2. yfinance holdings reconstruction (equity only)
3. Debt analytical model (`analytics/debt_returns_model.py` — uses each debt
   scheme's own real modified_duration_years + ytm_decimal, NOT a generic
   category average)
4. Synthetic regime-switching (last resort, itself shrunk toward a real
   asset-class-blended prior before ARISE ever sees it — see Section 5)

**Sandbox limitation**: this project was developed in an environment with
network egress blocked to Yahoo/AMFI/Alpha Vantage (confirmed via direct
failed requests, not assumed). Every fallback tier below the top one has
been exercised and verified working in this sandbox; the top tiers (real
live AMFI/yfinance calls) will only actually fire with real network access.
No code changes are needed to get real data once you have real network
access -- the fallback chain already prefers real data at every tier and
only degrades when a tier is unreachable.

---

## 4. FilterEngine -- Just Redesigned (Informed Consent, Not Hard Gates)

**The bug found and fixed**: `RISK_ALLOWED` was a hard allow-list. Every
one of the 20 real equity schemes carries `risk_score_1_to_6 == 6` ("Very
High") -- a genuine SEBI riskometer fact, confirmed against the real
factsheet, not a data error. Under a hard gate, conservative/moderate/
moderately_high investors got zero eligible equity schemes, ever -- same
bug independently found and fixed on the Flask prototype earlier in this
project's history, now fixed here too.

**The fix**: risk and goal (except `tax_saving`) are now soft advisory
flags, not hard blocks -- every scheme stays visible with a `risk_flag`
explaining any mismatch, matching how real Indian MF distribution actually
works (the riskometer exists for informed consent, not paternalistic
hiding). Hard blocks remain for genuinely objective/legal facts only:
- Age (min/max)
- Income minimum
- Investment amount (min/max) -- e.g., debt schemes have a real Rs 5,000 minimum
- Already-held schemes
- `tax_saving` goal + non-ELSS scheme (Section 80(C) eligibility is a legal
  fact about that specific scheme, not a preference)

**Equity sub-tiering**: real `std_deviation` values span 13.62%-20.89%
across the 20 equity schemes even though they share the same SEBI label --
used to label the equity risk-flag message as Core (<=16%) / Growth (16-18%)
/ Aggressive (>18%), enriching the disclosure without changing the
underlying `RISK_ALLOWED` trigger (kept uniform across equity+debt for
consistency and testability).

**API contract change**: `SchemeInfoResponse` (in `schemas/schemes.py`) now
has three new fields: `subtier` (equity only), `risk_flag` (None = no
mismatch), `risk_flag_level` ("match" | "mild" | "strong"). `filter_summary`
has two new informational counters: `risk_flagged`, `goal_flagged` (the
`by_risk`/`by_goal` counters now only reflect genuine hard blocks, which for
`by_risk` is always 0 now -- this is correct, not a bug).

**Verified**: all 64 filter tests pass, plus the full 13-step HTTP flow test
confirms a moderate-risk investor now correctly sees all 35 schemes
(appropriately flagged) instead of a hard-blocked subset.

---

## 5. ARISE v4 -- The Real Engine (`engines/arise_engine.py`, 793 lines)

This supersedes a block-bootstrap design that had a confirmed, serious
bug: resampling raw historical/synthetic returns directly let a single
lucky or unlucky draw get extrapolated undamped across a 25+ year horizon --
producing either a >100x runaway corpus or a 0%-success collapse on
identical data, depending on the random seed. Fixed via:

- **Student-t(df=5) AR(1) mean-reverting return process**, anchored on
  `PortfolioStatistics.portfolio_cagr`/`volatility` -- which are themselves
  shrunk toward an asset-class-blended real prior and evidence-scaled capped
  in `analytics/portfolio_statistics.py::PortfolioStatisticsBuilder.build()`
  before ARISE ever sees them. This is the critical fix: the shrinkage has
  to reach the actual bootstrap/simulation source, not just a separately
  reported diagnostic number (an earlier version of this fix reported a
  correct-looking shrunk number that the simulation never actually used --
  traced and fixed).
- **Blanchett spending smile** (India-calibrated: shallow decline through
  active years, genuine positive late-life leg since Indian healthcare
  inflation runs 12-14%/yr, unlike the shallower US curve)
- **Kitces ratchet strategy** (conservative floor, +10% upward-only ratchet
  when overfunded)
- **Configurable glide path**: declining (default) / rising (Pfau-Kitces) / static
- **ERN/CAPE valuation anchor** (optional, off unless a CAPE value supplied)
- **Milevsky reciprocal-gamma analytical cross-check** -- a closed-form ruin
  probability, reported as `milevsky_ruin_prob` purely as a diagnostic
  convergence guard, never the headline number. Expect this to diverge
  meaningfully from the Monte Carlo empirical result when there's a long
  accumulation phase (the closed-form assumes a fixed starting corpus at
  retirement with no accumulation-phase sequence risk; the MC correctly
  captures that risk) -- this is a known, documented limitation of the
  approximation, not a bug.
- **Strategy leaderboard**: robotic / guardrails / vpw / ratchet compared on
  common random numbers, with an income-volatility metric.

**Real Indian tax law**: `LTCG_EXEMPTION = 125_000`, `LTCG_RATE = 0.125` --
tax facts, not market assumptions. Two-bucket liquidity sequencing (Liquid
Reserve + Growth) for decumulation, tax-aware withdrawal waterfall.

**Contract**: `run_monte_carlo()`'s signature and every original `ARISEResult`
field are unchanged from the pre-v4 design -- all v4 additions are additive.
`ARISERequest` (API schema): `n_simulations`, `life_expectancy`, `slab_rate`,
`strategy` (`robotic`|`adaptive`|`vpw`), `step_up`.

**Verified live over real HTTP** (not just direct function calls):
`engine_version: "v4"`, `milevsky_ruin_prob` present, `strategy_leaderboard`
with 4 entries, `adaptive_success_rate` computed correctly for a real
selected portfolio.

---

## 6. Full API Surface

```
POST /api/v1/auth/register        {email, password, full_name} -> access+refresh tokens
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout          {refresh_token}
POST /api/v1/auth/logout-all
GET  /api/v1/auth/me

POST /api/v1/profile              Deep Profile -- ALL 10 fields required (see ProfileCreate schema)
GET  /api/v1/profile
PUT  /api/v1/profile               partial update

GET  /api/v1/schemes/filtered      Filter Engine output (eligible + filtered_out + summary)
POST /api/v1/schemes/select        {selected_scheme_ids: [...]}  <- field name is selected_scheme_ids
POST /api/v1/schemes/allocate      set weights across selected schemes
GET  /api/v1/schemes/selected

GET  /api/v1/analysis/overlap      Jaccard heatmap + network graph
GET  /api/v1/analysis/plos         10-layer PLOS + top-5 recommendation
GET  /api/v1/analysis/risk         Ledoit-Wolf, VaR/CVaR, Monte Carlo GBM
POST /api/v1/analysis/arise        ARISE v4 retirement projection (Section 5)
GET  /api/v1/analysis/prices       NSE prices for selected holdings (yfinance, delayed)

GET  /api/v1/market/portfolio-value, /dashboard, /pnl, /fundamentals/{ticker}, /nav/{scheme_id}
POST /api/v1/market/tasks/download-prices, /download-nav, /refresh-holdings   (Celery triggers)

POST /api/v1/reports/generate      PDF report
GET  /api/v1/reports/{report_id}, /{report_id}/download

GET  /api/v1/notifications/mine
POST /api/v1/notifications/{notification_id}/read

WS   /api/v1/ws/live-prices
```

All Phase 3 analysis endpoints require: valid JWT + completed profile + at
least one scheme selected. Returns 404 "Scheme selection not found" if
selection is missing.

---

## 6b. Application Layer (built on top of the engines)

The engines/analytics/data_loaders/schemas in this repo are the pre-existing,
verified core. Everything that turns them into a running service was built
around them WITHOUT modifying them:

    core/         config, security, deps, errors, json, async_utils, logging
    infrastructure/ db, redis_client, timescale
    models/       User, RefreshToken, InvestorProfile, SchemeSelection, Report, Notification
    services/     auth, profile, scheme, analysis, market, report, notification, engine_registry
    api/v1/       the 32 endpoints in Section 6
    tasks/        Celery app + nightly price/NAV refresh
    alembic/      0001_initial (verified: zero drift vs the ORM)

Four modules were HARD CONTRACTS -- the pre-existing code already imported them:
    backend.core.config              settings.{DATABASE_URL, ALPHA_VANTAGE_API_KEY,
                                     AMFI_BASE_URL, AMFI_NAV_REFRESH_DAYS}
    backend.core.async_utils         run_async_safely(coro)
    backend.infrastructure.redis_client   cache_get / cache_set / publish
    backend.infrastructure.timescale      bulk_insert_prices / bulk_insert_corporate_actions

Do not rename those without updating their call sites.

Two structural rules the app layer relies on:
  - Engines are CPU-heavy and synchronous, so every engine call is offloaded via
    `anyio.to_thread.run_sync`. This keeps the event loop free AND lets
    run_async_safely take its cheap asyncio.run path (a worker thread has no loop).
  - Selection weights are FRACTIONS SUMMING TO 1.0, never percentages --
    PortfolioStatisticsBuilder.build() raises otherwise.

---

## 6c. Bugs Found And Fixed While Building The App Layer

**(1) Every debt scheme was modelled as risk-free.** `debt_returns_model._anchor_vol`
reads `modified_duration_years` / `ytm_decimal`, but the factsheet data ships
`modified_duration: "10.22 Years"` and `ytm: "7.67%"` -- STRINGS under DIFFERENT
keys. So the lookup returned None for all 15 debt schemes and fell through to
CREDIT_LIQUIDITY_VOL_FLOOR (20bps): a 10.22y-duration gilt fund was as volatile
as an overnight fund. Section 3's claim that Tier 3 "uses each debt scheme's own
real modified_duration + ytm, NOT a generic category average" was silently NOT
happening.

Fixed WITHOUT touching the engine (its contract is the correct one) --
`scripts/normalize_scheme_metadata.py` additively derives the numeric keys the
engine documents, preserving the original factsheet strings. Gilt vol now 10.47%
(was 0.20%); overnight correctly stays at the floor. CI runs `--check` to prevent
regression. DO NOT re-import raw factsheet data without re-running it.

**(2) The v4 ARISE fields never reached the client.** Section 5 says
`engine_version` / `milevsky_ruin_prob` / `strategy_leaderboard` were verified
over real HTTP, but `ARISEResponse` didn't DECLARE them, so Pydantic dropped
every one. Now declared (additively). They also default to float("nan") in
ARISEResult, and NaN is NOT valid JSON -- it throws in JSON.parse in every
browser. `core/json.py::SafeJSONResponse` now scrubs non-finite floats to null
app-wide (a correlation against a flat debt NAV is 0/0 -> NaN, so this was live).

---

## 6d. Production Hardening (the transaction ledger, the real SIP, real rate limiting)

Three things stood between the app layer and production. All three needed a data
model, not a workaround.

**(1) THE TRANSACTION LEDGER -> real P&L.** The platform stored an *intended
allocation*, not a transaction history, so no cost basis existed and /market/pnl
had to answer `pnl_available: false`. Added `transactions` (units, nav, amount,
txn_date, external_ref) + `analytics/portfolio_accounting.py` (pure: FIFO lot
matching, Indian CGT, XIRR) + `services/portfolio_service.py` + /api/v1/portfolio/*.

  - Units/NAVs are **Decimal, never float** -- float error across 240 SIP
    instalments stops the holding reconciling with the AMC's own statement.
  - **FIFO** lot matching, oldest units first (what registrars and the IT Act use).
    Partial lot consumption preserves the ORIGINAL buy date -- resetting it would
    silently restart the holding-period clock and turn LTCG into STCG.
  - **Tax (Finance Act 2024, w.e.f. 23 Jul 2024)**: equity STCG 20% (<=12m),
    LTCG 12.5% (>12m) above a Rs 1,25,000 ANNUAL AGGREGATE exemption (aggregate,
    not per-scheme). Debt acquired on/after 1 Apr 2023 = "specified mutual fund"
    (s.50AA): ALL gains slab-rate regardless of holding period -- there is no
    indexed 20% LTCG any more. These constants agree with arise_engine's own
    LTCG_EXEMPTION/LTCG_RATE, so decumulation and P&L cannot disagree about tax.
  - **XIRR**, not value/cost-1: a naive total return flatters a SIP because it
    pretends last month's contribution has had a year to compound.
  - `external_ref` = idempotency key. A retried POST must not double-book a
    purchase; a duplicate buy silently doubles the cost basis and corrupts every
    tax number downstream. Checked BEFORE insert -- once an IntegrityError escapes
    an async commit, SQLAlchemy's greenlet context is gone and any further query
    on that session raises MissingGreenlet, so "insert, catch, look up the
    original" cannot work on an async session.
  - Positions are ALWAYS replayed from the ledger, never denormalised into a
    running balance -- a back-dated correction is routine and would drift it.

  /market/pnl is kept as a DEPRECATED alias that delegates to /portfolio/pnl.

**(2) REAL SIP.** `investor_profiles.monthly_sip` + `.sip_step_up_pct` (both
NULLABLE -> the documented 10-field ProfileCreate contract in Section 6 is
unchanged). ARISE precedence: request > profile > 20%-of-income ESTIMATE. The
estimate now only backstops legacy profiles and always sets
`assumptions.monthly_sip_is_estimated = true`.

**(3) DISTRIBUTED RATE LIMITING.** The in-process limiter meant N replicas = N x
the configured limit. Replaced with a Redis sliding-window counter
(`infrastructure/rate_limit.py`) executed as an ATOMIC Lua script -- run as
separate round-trips, two replicas both read `limit-1` and both allow (classic
check-then-act race, exactly when under attack). Sliding, not fixed-window.
Keyed on the authenticated user where present, IP otherwise. FAILS OPEN to the
local limiter if Redis dies -- a limiter that 500s when its cache dies has turned
a degradation into an outage.

Migration: `0002_transactions_and_sip` (additive; no backfill needed).

---

## 7. Test Status -- 137/137 Passing

```bash
python3 -m pytest tests/ -q
# 137 passed in ~39s
```

Test strategy: in-memory SQLite (not Postgres) via `tests/conftest.py`'s
`client` fixture -- an `httpx.AsyncClient` wired to the real FastAPI app with
`get_db` overridden. No Docker/Postgres needed to run the test suite.

The full 13-step HTTP flow (register -> profile -> filter -> select -> allocate
-> selected -> overlap -> plos -> risk -> arise -> refresh -> logout) is now
PERSISTED as `tests/test_e2e_flow.py` and runs in CI -- it no longer needs to be
recreated by hand each session.

Suites: test_e2e_flow (the journey), test_auth (rotation/reuse/token-confusion),
test_profile_schemes (incl. the Section 4 regression guard), test_analysis
(matrix symmetry, VaR ordering, ARISE determinism, no-NaN), test_security
(IDOR + error leakage), test_infrastructure (the Section 6c fixes),
test_portfolio (FIFO, Indian CGT hand-computed, XIRR, idempotency, ledger
isolation, rate limiter), and test_reports_market (real PDF, full OpenAPI).

---

## 8. Running This Project

**For real deployment** (Docker, Postgres, Redis):
```bash
docker-compose up -d
docker-compose exec api alembic upgrade head   # DB migrations
# API at http://localhost:8000, docs at /docs
```

**For local development/testing without Docker**: the test suite (Section 7)
runs against in-memory SQLite with zero external dependencies -- this is the
fastest way to verify engine/API correctness without standing up the full
stack.

**Environment variables** (`.env.example` has the full list):
- `DATABASE_URL` -- Postgres connection string
- `SECRET_KEY` -- JWT signing key (must be changed from the placeholder for any real deployment)
- `REDIS_URL`, `REDIS_ENABLED`
- `ALPHA_VANTAGE_API_KEY` -- optional, enables richer fundamentals
- `ARISE_DEFAULT_SIMULATIONS`, `ARISE_MAX_SIMULATIONS`

---

## 9. Known Limitations / Honest Gaps

- Per-stock return/volatility (used in some risk/PLOS layers) has graceful
  fallbacks when unavailable -- check `risk_engine.py`/`plos_engine.py` for
  the exact fallback values used (documented inline).
- Three equity schemes (Innovation, Active Momentum, MNC funds) are genuinely
  <3 years old and lack `std_deviation`/full return history in the real
  factsheet -- `equity_subtier()` defaults these to "Core" (gentlest tier)
  rather than fabricating a volatility number; MNC fund specifically has no
  return history at all (inception July 2025) and may need explicit
  handling in any code path that assumes every scheme has a `ret` value.
- This sandbox cannot reach yfinance/AMFI/Alpha Vantage directly (confirmed,
  not assumed) -- every fallback tier below real-network-data has been
  exercised; only real-network verification remains, and needs no code
  changes to activate once network access exists.
- Milevsky vs Monte Carlo divergence is a known, explained approximation
  limitation (Section 5), not a bug -- don't "fix" it by making the
  closed-form formula agree more closely; that would mean weakening its
  independence as a genuine cross-check.

---

## 10. What NOT To Do

- Don't reintroduce a hard risk/goal gate in FilterEngine -- that's the
  exact bug this session fixed, confirmed via real usability failure (zero
  eligible equity schemes for most real investor profiles).
- Don't let ARISE's return-generation process consume raw
  historical/synthetic returns directly without the shrinkage/cap step in
  `portfolio_statistics.py` reaching it -- that's the exact bug that caused
  both a >100x runaway corpus and a 0%-success collapse on identical data.
- Don't treat `milevsky_ruin_prob` as ground truth or try to make it match
  the Monte Carlo result more closely -- it's an intentionally independent
  cross-check with a known, explained divergence source.
- Don't confuse this backend with the Flask prototype
  (`nippon-fund-overlap-analyzer`) built earlier in this project's history --
  that was explicitly a simpler, demo-data-adjacent side project; this
  FastAPI backend with 35 real schemes and ARISE v4 is the actual product.
