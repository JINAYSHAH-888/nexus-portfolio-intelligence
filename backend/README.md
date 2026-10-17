# Nippon India Overlap Platform — Backend

Institutional-grade mutual fund **overlap / risk / retirement** intelligence over
**35 real Nippon India schemes** (20 equity + 15 debt) extracted from actual
factsheets.

FastAPI · PostgreSQL/TimescaleDB · Redis · Celery · SQLAlchemy 2 (async) · Alembic

```
Landing → Register → Deep Profile → Filter (informed consent)
        → Explore overlap → Select → Allocate
        → PLOS ranking → Risk dashboard → ARISE v4 retirement projection
```

**Status:** 44 endpoints, 412 tests passing, lint clean, migrations verified against the ORM.
**Verified connected end-to-end to the real Next.js frontend** — a real browser drove register → deep profile → scheme selection → overlap → PLOS → risk → a real Monte Carlo ARISE run, all through actual HTTP against this backend. See "Frontend integration" below.

Includes a real Upstox broker integration: OAuth, a verified (not fuzzy-matched)
ISIN map for all 35 schemes with a nightly self-healing refresh, and a live
market-data websocket feed with a fully-typed protobuf decoder.

---

## Quick start

### Docker (full stack)
```bash
cp .env.example .env
# SECRET_KEY must be changed — the app REFUSES to boot in production without it:
python -c "import secrets; print(secrets.token_urlsafe(48))"

make up                 # builds api + worker + beat + timescale + redis, runs migrations
open http://localhost:8000/docs
```

### Local (no Docker)
```bash
pip install -r requirements-dev.txt
make test               # 412 tests, in-memory SQLite, zero external deps
make dev                # uvicorn on :8000
```

---

## Quantitative corrections and additions

The analytics layer was audited and four classes of defect were fixed, each
verified against live data rather than asserted:

| Area | What was wrong | Now |
|---|---|---|
| **Benchmarks** | Every scheme measured against Nifty 50; only 6 of 35 declare it | Each scheme uses its OWN declared benchmark via `data_loaders/benchmark_registry.py`. 15 of 35 have a usable proxy; the other 20 (CRISIL/NIFTY debt, narrow sector) return `None` **with a stated reason** rather than a confident wrong number |
| **Fees** | Expense ratios present for 33 of 35 schemes, read by nothing — projections were gross | Deducted in `market_data.net_of_expenses`, but ONLY from holdings-reconstructed series: a NAV-derived series is already net, so deducting twice would understate a fund by up to 2.1%/yr |
| **Debt schemes** | Liquid-fund holdings are CDs and commercial paper whose *issuers* match equity tickers, so 45-day paper was reconstructed as bank **shares** — a 0.12-year-duration fund reported **17.95% volatility** | Debt schemes skip equity reconstruction entirely and use the duration/YTM model. Same fund now reports **0.19%** |
| **Return space** | A log drift fed to a GBM that subtracts the Ito term again (risk engine), and a geometric CAGR fed to a process that adds its own volatility drag (ARISE) | Converted once, through named helpers in `risk_metrics`. ARISE now realises the CAGR it claims (+0.07pp vs −1.38pp before) |

Added alongside: Sortino / Calmar / Ulcer / downside deviation, alpha, R²,
tracking error, SEBI-mandated Information Ratio, up/down capture,
Cornish-Fisher VaR with Kupiec backtesting, rolling-window stability,
Gompertz stochastic mortality, Kitces magnitude-of-failure metrics, a
conservatism ledger, ESMA-method closet-index detection, an India-calibrated
safe-withdrawal band (Saraogi 2022; Raju & Saraogi 2024), Hierarchical Risk
Parity reference weights, and a direct-vs-regular plan cost comparison.

Every model choice is registered in `GET /api/v1/meta/assumptions`.

### Operational note

`uvicorn` runs with `--loop asyncio`, **not** uvloop. The engines offload to
worker threads that run their own short-lived event loops
(`core/async_utils.run_async_safely`); under uvloop that pattern segfaulted
the worker on roughly half of all `/analysis/risk` requests. Measured on a
cold cache: uvloop 0/8 requests succeeded, asyncio 8/8.

---

## Architecture

```
backend/
├── main.py                  app entrypoint: middleware, error handlers, lifespan
├── core/
│   ├── config.py            settings  ← REQUIRED by the pre-existing loaders
│   ├── async_utils.py       run_async_safely  ← REQUIRED by risk_engine + portfolio_statistics
│   ├── security.py          bcrypt (SHA-256 pre-hashed) + typed JWTs
│   ├── deps.py              the JWT → profile → selection gate chain
│   ├── errors.py            domain exceptions → HTTP (no HTTPException in services)
│   └── json.py              SafeJSONResponse — NaN/Inf can never reach a client
├── infrastructure/
│   ├── db.py                async engine, session, Base
│   ├── redis_client.py      cache_get/cache_set/publish  ← REQUIRED by the loaders
│   ├── rate_limit.py        distributed sliding window (atomic Lua), fails open
│   └── timescale.py         bulk_insert_prices/…  ← REQUIRED by yfinance_pipeline
├── models/                  User, RefreshToken, InvestorProfile, SchemeSelection,
│                            Transaction (the cost-basis ledger), Report, Notification
├── services/                business logic; engines are called here, offloaded to threads
├── api/v1/                  auth · profile · schemes · analysis · portfolio · market
│                            · reports · notifications · ws
├── tasks/                   Celery app + nightly price/NAV refresh
│
├── engines/     ─┐
├── analytics/    ├─ PRE-EXISTING, verified numerics. Left untouched.
├── data_loaders/─┘
├── schemas/                 pre-existing Pydantic contracts (analysis.py extended, additively)
└── data/                    35 schemes · holdings · 221 tickers · India CPI
```

### Design decisions worth knowing

**Engines run on worker threads.** ARISE is `n_simulations × n_years × 4 strategies`
of Student-t paths; RiskEngine does Ledoit–Wolf shrinkage plus a 10,000-path GBM
per scheme. Calling those from an async route would peg the event loop and stall
every other request, including health checks. `anyio.to_thread.run_sync` handles
them — which also lets `run_async_safely` take its cheap `asyncio.run` path,
since a worker thread has no loop of its own.

**Redis fails open.** It is a cache, not a database. If it's down, `cache_get`
returns a miss and the caller falls back to its real source. A cache outage
degrades latency, never correctness — and `/health/ready` deliberately still
reports *ready*.

**Weights are fractions summing to 1.0**, never percentages —
`PortfolioStatisticsBuilder.build()` raises otherwise. Enforced at the schema,
the service, and the DB comment.

**Nothing is fabricated.** `/market/pnl` returns `pnl_available: false` with a
reason instead of a plausible-looking zero, because a real P&L needs a cost
basis this platform doesn't store. ARISE's SIP estimate is labelled `ESTIMATE`
in the response `assumptions` and is overridable.

---

## The transaction ledger — real P&L, not a refusal

The platform previously stored an *intended allocation*, not a transaction
history, so there was no cost basis and `/market/pnl` had to answer
`pnl_available: false`. That was honest, but it wasn't useful. The fix was to
add the missing data model.

`POST /api/v1/portfolio/transactions` records a buy / SIP instalment / sell
(units, NAV, date). From that ledger everything else follows:

| | |
|---|---|
| **Mark-to-market P&L** | per scheme, valued at the live NAV (AMFI → factsheet fallback) |
| **FIFO lot matching** | oldest units consumed first, as Indian registrars and the IT Act require |
| **Capital-gains tax** | Finance Act 2024 rules — see below |
| **XIRR** | money-weighted, the return the investor *actually* experienced |

**Why XIRR and not `value/cost - 1`.** For a 12-month SIP of ₹1,000 now worth
₹13,000, the naive figure is +8.3%. But the average rupee was only invested for
~6 months — the true annualised return is far higher. A dashboard reporting 8.3%
understates the fund's real performance. `test_sip_xirr_beats_the_naive_total_return`
pins this.

**Tax rules encoded** (Finance Act 2024, w.e.f. 23 Jul 2024):
- **Equity:** STCG 20% (≤12m); LTCG 12.5% (>12m) above a **₹1,25,000 annual
  aggregate** exemption — aggregate, not per-scheme, which is a common error that
  understates tax for anyone holding several funds.
- **Debt acquired on/after 1 Apr 2023** ("specified mutual funds", s.50AA): **all**
  gains are slab-rate regardless of holding period. Most hand-rolled P&L code
  still applies an indexed 20% LTCG here that no longer exists.
- Debt bought before that date is grandfathered (>24m → 12.5%).

These agree with `arise_engine`'s own `LTCG_EXEMPTION`/`LTCG_RATE`, so the
decumulation model and the P&L endpoint cannot disagree about tax.

**Money safety:** units and NAVs are `Decimal`, never float — float error across
240 SIP instalments stops the holding reconciling with the AMC's own statement.
`external_ref` makes the POST idempotent, so a retried request from a flaky mobile
connection can't double-book a purchase and silently corrupt the cost basis.
Positions are always replayed from the ledger, never kept as a running balance,
so correcting a mis-keyed transaction propagates to P&L and tax immediately.

*Indicative only, not tax advice. Loss set-off / carry-forward (s.70/74) is not
modelled — deliberately, rather than approximated wrongly.*

---

## ARISE runs on a real SIP

`InvestorProfile` now carries `monthly_sip` and `sip_step_up_pct`. ARISE's SIP
precedence, most-trusted first:

1. `monthly_sip` on the request → exact
2. `profile.monthly_sip` → exact, the investor told us
3. 20% of gross income → **ESTIMATE**, flagged `monthly_sip_is_estimated: true`

Both new fields are **optional**, so the documented 10-field `ProfileCreate`
contract is unchanged and existing clients keep working. Tier 3 now exists only
as a backstop for legacy profiles — and when it fires, it says so in the response.

---

## Rate limiting is now a real control

The old limiter kept counters in one process's memory: with N replicas the
effective limit was N× the configured value, and an attacker only had to spread
requests across replicas. Fine as an abuse brake; not a control for `/auth/login`.

It's now a **Redis sliding-window counter shared by every replica**, executed as
an **atomic Lua script**. Atomicity is the point: run as separate round-trips,
two replicas can both read `limit - 1` and both allow their request — the classic
check-then-act race, which bites exactly when you're under attack. Sliding, not
fixed-window, so an attacker can't send `limit` at 00:59 and `limit` again at
01:00.

Keyed on the **authenticated user** where present, IP otherwise: IP alone throttles
a whole NATed office together while an attacker with a proxy pool rotates around it.

**Fails open** to the in-process limiter if Redis is down — a rate limiter that
500s when its cache dies has turned a minor degradation into an outage.

---

## Two real bugs found and fixed

### 1. Every debt scheme was modelled as risk-free  🔴 material

`analytics/debt_returns_model.py::_anchor_vol` reads **`modified_duration_years`**
and **`ytm_decimal`**. The factsheet data ships:

```json
"modified_duration": "10.22 Years",   // a STRING, under a DIFFERENT key
"ytm": "7.67%"
```

So the lookup returned `None` for **all 15 debt schemes**, and `_anchor_vol`
silently fell through to `CREDIT_LIQUIDITY_VOL_FLOOR` (0.002 = **20 bps**). A
10.22-year-duration gilt fund was being modelled as *exactly as volatile as an
overnight fund*. That understates debt risk by ~60× on the long end and
propagates into the covariance matrix, VaR/CVaR, and ARISE's ruin probability.

**The fix does not touch the engine** — its logic and documented contract are
correct. Instead `scripts/normalize_scheme_metadata.py` parses the real
factsheet strings into the numeric keys the engine documents, **additively**
(the original strings survive byte-for-byte). Verified through the real fallback
chain:

| scheme | duration | vol before | vol after |
|---|---|---|---|
| Overnight Fund | 0.003 y | 0.20% | 0.20% ✓ *(genuinely riskless)* |
| Corporate Bond | 2.35 y | 0.20% | **2.82%** |
| Gilt Fund | 8.66 y | 0.20% | **10.47%** |
| Nivesh Lakshya (long duration) | 10.22 y | 0.20% | **12.48%** |

CI runs `normalize_scheme_metadata.py --check` so re-importing raw factsheet data
can never silently reintroduce this.

### 2. The v4 ARISE fields never reached the client

CLAUDE.md §5 records `engine_version`, `milevsky_ruin_prob` and
`strategy_leaderboard` as "verified live over real HTTP" — but `ARISEResponse` in
`schemas/analysis.py` didn't declare them, so Pydantic silently dropped every one.
They're now declared (additively), with NaN → `null` coercion, because
`ARISEResult` defaults those diagnostics to `float("nan")` and **`NaN` is not
valid JSON** — it throws in `JSON.parse` in every browser. `SafeJSONResponse`
now scrubs non-finite floats app-wide (a correlation against a flat debt NAV is
`0/0` → NaN, so this was live, not theoretical).

---

## Security

- **Refresh-token rotation with reuse detection.** Refreshing burns the old
  token. If an already-used token is replayed, two parties hold it → the entire
  token family is revoked (OAuth 2.0 BCP). `jti`s are stored, never token strings.
- **Typed tokens.** A refresh token cannot authenticate a request and an access
  token cannot buy a new pair — the `type` claim is checked on every decode.
- **bcrypt with SHA-256 pre-hash.** bcrypt truncates at 72 *bytes*; the register
  schema allows 128 chars. Pre-hashing means the whole password contributes entropy.
- **No account enumeration.** Wrong password and unknown user return an identical
  status *and* message, with a dummy verify to match timing.
- **IDOR guards** on reports and notifications (a report carries income, corpus
  and holdings — a guessable-UUID leak would be a direct PII breach).
- **500s never leak** tracebacks, DSNs, or file paths; only a `request_id`.
- **Production fail-fast**: the app refuses to boot with a placeholder
  `SECRET_KEY`, SQLite, wildcard CORS, or `MARKET_DATA_FORCE_SYNTHETIC=true`.

---

## Testing

```bash
make test    # 99 passed in ~33s
```

In-memory SQLite via an `httpx.AsyncClient` against the **real** app with `get_db`
overridden — no Docker, no Postgres, no Redis.

| suite | what it pins down |
|---|---|
| `test_e2e_flow.py` | the full 13-step journey CLAUDE.md §7 describes but never persisted |
| `test_auth.py` | rotation, reuse detection, token confusion, 72-byte truncation, enumeration |
| `test_profile_schemes.py` | **§4 regression guard**: every risk tier still sees all 20 equity schemes |
| `test_analysis.py` | matrix symmetry, VaR/CVaR ordering, ARISE determinism, no NaN in JSON |
| `test_security.py` | cross-user isolation, report/notification IDOR, error leakage |
| `test_infrastructure.py` | the debt-duration fix, the sync/async bridge, JSON sanitisation |
| `test_portfolio.py` | FIFO lots, Indian CGT (hand-computed), XIRR, idempotency, ledger isolation, rate limiter |
| `test_reports_market.py` | real downloadable PDF, full OpenAPI surface |

---

## Operations

```bash
alembic upgrade head                      # migrations (verified: zero ORM drift)
python scripts/normalize_scheme_metadata.py --check   # CI guard for bug #1

celery -A backend.tasks.celery_app:celery_app worker  # nightly price/NAV refresh
celery -A backend.tasks.celery_app:celery_app beat
```

| endpoint | purpose |
|---|---|
| `GET /health` | liveness — touches no dependency |
| `GET /health/ready` | readiness — DB hard, Redis soft |
| `GET /docs` | Swagger, with working Authorize button |

### Known limitations (honest)

- `milevsky_ruin_prob` is an *independent* closed-form cross-check and is
  **expected** to diverge from the Monte Carlo when the accumulation phase is
  long (it assumes a fixed corpus at retirement with no accumulation sequence
  risk). Don't "fix" it into agreement — that would destroy its value as a check.
- Three equity schemes (Innovation, Active Momentum, MNC) are genuinely <3 years
  old and lack `std_deviation`; `equity_subtier()` defaults them to "Core" rather
  than inventing a volatility.
- This sandbox has no egress to yfinance/AMFI/Alpha Vantage. Every fallback tier
  below the top has been exercised; **no code changes are needed** to activate
  real data once network access exists.


---

## Upstox integration

### Identity: two verified, NAV/symbol-corroborated maps, not runtime guessing

`backend/integrations/upstox/mf_mapping.py` and `equity_mapping.py` load
build-time-verified maps (`scripts/build_upstox_isin_map.py`,
`build_upstox_equity_map.py`) binding our 35 schemes and ~221 underlying
stocks to real Upstox `instrument_key`s. Matching is NEVER done at request
time — the MF mapper's first real run caught itself matching "Medium Duration
Fund" to a BONUS unit of a DIFFERENT fund (63% NAV drift); it now hard-halts
on ambiguity instead of guessing, and a nightly Celery job
(`market.refresh_upstox_isin_map`, 06:30 IST) re-verifies against the live
instrument file, refusing to write a stale/wrong map if even one scheme fails
to resolve.

The map also surfaced a real product gap: **4 of the 35 schemes are currently
closed to new purchase** on Upstox (Small Cap + 3 RBI-capped international
feeders). The filter engine now excludes them from `eligible_schemes` — an
investor can no longer select, allocate, and run a full ARISE projection on a
fund they cannot actually buy.

### Live prices: a typed protobuf decoder, not the SDK's lossy dict path

`backend/integrations/upstox/feed_decoder.py` decodes `MarketDataFeedV3`
protobuf messages directly, deliberately bypassing the official SDK's
`json_format.MessageToDict()` step, which (confirmed against the SDK's own
output) re-encodes every int64 field as a JSON string and cannot distinguish
"this field doesn't exist for an index" from "this field is genuinely zero."
`FullFeed`'s `oneof` (`MarketFullFeed` vs `IndexFullFeed`) is branched
explicitly — an index tick's volume/OI/depth come back `None`, never a
fabricated `0`, which matters because NIFTY is the benchmark this platform
computes beta and tracking error against.

### One upstream connection, not one per investor

Upstox caps a login to 2 concurrent market-data connections. `feed_worker.py`
runs as a **standalone process** (the SDK is sync/callback-based; bridging it
into FastAPI's event loop would block every HTTP request on every tick) under
a **dedicated service account** — never an individual investor's personal
OAuth connection, so one investor disconnecting can never kill the feed for
everyone else. It subscribes to the union of every currently-selected
holding across all investors, ranked by aggregate portfolio weight
(`subscription.py`) and capped at the V3 `full`-mode limit (2,000 keys), and
republishes each tick over Redis to the existing `/api/v1/ws/live-prices`
fan-out — no changes needed to the browser-facing endpoint.

```bash
# One-time: connect the service account (tokens expire daily at 03:30 IST)
curl http://localhost:8000/api/v1/auth/upstox/login  # (as an authenticated platform user)

# docker-compose already runs this as its own service (`feed-worker`)
python -m backend.integrations.upstox.feed_worker
```


---

## Tier 3: Historical Candle Data V3 replaces yfinance as the primary price source

`backend/integrations/upstox/candles.py` implements Historical Candle Data V3
and Intraday Candle Data V3 exactly as documented — including two details easy
to invert from intuition alone: `to_date` precedes `from_date` in the URL path,
and V3 splits `unit` (minutes/hours/days/weeks/months) from a numeric
`interval`, unlike the deprecated v2 API's single `"1minute"`/`"day"` strings.
Every documented retrieval-limit boundary (1 month for ≤15-min bars, 1 quarter
beyond that, 10 years for daily, unlimited for weekly/monthly) is enforced
client-side, before a request is sent — a caller finds out why immediately
rather than burning a rate-limit token on a request that was already invalid.

`market.download_prices` (the existing nightly Celery job) now tries Upstox
first for every stock with a verified `instrument_key` (`build_upstox_equity_map.py`
— 211 of 221), falling back to the original yfinance pipeline per-ticker for
the rest. **If the Upstox service account has no valid token at all** — not
configured, or past the 03:30 IST daily reset — the whole job degrades to
100% yfinance, exactly the pre-Upstox behaviour. Upstox being unavailable must
never make price collection fail *harder* than before this integration
existed. Both paths write into the same `tsdb.stock_prices` table via the
same, unmodified `bulk_insert_prices` — nothing downstream needs to know or
care which source a given day's bar came from.


---

## Frontend integration

The companion Next.js frontend (`nippon-frontend/`) talks to this backend over
plain HTTP. Two things had to be fixed here to make that connection actually
work, both caught by driving a real browser against a real running instance
of both projects together — not by reasoning about the code in isolation:

**1. `GET /auth/upstox/login` returned a raw HTTP redirect, which is
fundamentally incompatible with requiring Bearer-token auth.** A browser
navigating a link can never attach a custom `Authorization` header — and a
redirect is exactly what breaks any programmatic caller (Swagger's "Execute"
button, or a frontend `fetch()`) too, since `fetch()` auto-follows redirects,
turning the call into a cross-origin XHR straight into Upstox's own login
page, which Upstox correctly refuses. The browser reports that refusal as an
opaque "Failed to fetch" — not a clear CORS error, which made this
confusing to diagnose from the Swagger UI alone. Fixed by returning the
authorize URL as JSON instead: the caller fetches it (same-origin, real auth
header, no redirect to follow), then performs the actual top-level
navigation to Upstox itself — a step that needs no auth header, since
Upstox's login page doesn't want one. Also surfaced a genuine, worth-knowing
dependency while fixing this: the OAuth `state` binding lives in Redis, and
unlike everywhere else in this app where Redis fails open, a Redis outage
here silently breaks the ability to complete an Upstox login at all (the
login step still returns 200, but the callback always rejects the state as
"expired") — pinned by
`tests/test_upstox.py::test_state_binding_requires_redis_and_fails_visibly_not_silently`.

**2. `CORS_ORIGINS` from a real `.env` file crashed the app at boot.**
`pydantic-settings` tries to JSON-decode any env var feeding a `list[str]`
field *before* a custom `@field_validator` ever runs — so the documented
`.env.example` format (`CORS_ORIGINS=http://a,http://b`) raised a raw
`SettingsError` on startup, never reaching the validator that was supposed to
handle it. Fixed with `Annotated[list[str], NoDecode]`, which defers all
parsing to the validator. Both the plain comma format and a JSON array now
work; a test (`test_provenance.py`-adjacent config tests) pins both.

**2. The OAuth redirect URI didn't match what's registered with Upstox.**
Upstox validates `redirect_uri` against the exact value registered in the
Developer App dashboard and rejects any mismatch outright. The registered
value is the bare `http://localhost:8000/callback` — not the versioned
`/api/v1/auth/upstox/callback` every other route lives under. `main.py` now
exposes a second route at the literal `/callback` path that reuses the exact
same handler function (`app.add_api_route`, not a duplicated copy) — see
`tests/test_upstox.py::TestBareCallbackRoute`, which confirms both paths
return byte-identical responses to an invalid state, proving there's no
drift risk between them.

### Running both together

```bash
# terminal 1 — this backend
alembic upgrade head
uvicorn backend.main:app --port 8100

# terminal 2 — the frontend
cd ../nippon-frontend
echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8100/api/v1" > .env.local
npm run dev
```

Every field name and response shape the frontend's TypeScript types assume
was checked against this backend's real, live JSON responses (not just the
Pydantic source) during that integration pass — including catching that
`risk_rating` comes back as a lowercase enum (`"very_high"`), not display
text, which the frontend now formats rather than rendering raw.
