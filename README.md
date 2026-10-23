# Nexus Portfolio Intelligence

Mutual fund **overlap, risk and retirement** analytics for Indian investors, built on
**35 real Nippon India schemes** (20 equity + 15 debt) taken from published factsheets.

> Most portfolios of five or six funds quietly hold the same stocks twice.
> Nexus shows you exactly where — and what it means for your retirement.

**Example finding (real factsheet data, 31 May 2026):** HDFC, ICICI, Axis and SBI each
sit in 11 of Nippon India's 20 equity funds, and 8 funds hold all four. Large Cap and
Quant share only 15 holdings, yet **41% of their money** is in the same stocks.

---

## Contents
- [Features](#features)
- [Architecture](#architecture)
- [Tech stack](#tech-stack)
- [Getting started](#getting-started)
- [Configuration](#configuration)
- [Upstox integration](#upstox-integration)
- [Analytics engines](#analytics-engines)
- [Frontend](#frontend)
- [Testing & quality](#testing--quality)
- [Project structure](#project-structure)
- [Data & honesty principles](#data--honesty-principles)
- [Documentation](#documentation)
- [Disclaimer](#disclaimer)

---

## Features

| Area | What it does |
|---|---|
| **Deep profile** | 10-field investor profile (age, income, corpus, goal, risk appetite, retirement age, SIP…) drives every analysis |
| **Scheme filter** | All 35 schemes stay visible; mismatches are *flagged with reasons*, never silently hidden (informed consent) |
| **Holding overlap** | Shared names (Jaccard) **and** shared money (Σ min weight), pair by pair |
| **PLOS score** | 10-layer Portfolio-Level Overlap Score — every layer's contribution shown |
| **Risk** | Ledoit–Wolf covariance, Sharpe/Sortino/Calmar, VaR/CVaR (historical, parametric, Cornish–Fisher), Kupiec backtest, beta, HRP reference allocation |
| **ARISE v4** | Retirement Monte Carlo: Student-t AR(1) returns, Gompertz lifespans, 4 withdrawal strategies, Indian tax law, estimation risk |
| **Live market data** | Upstox OAuth integration, AMFI NAV history, yfinance fallback chain |
| **Dashboard** | Overlap, diversification, risk and retirement readings at a glance |
| **Themes** | Light / dark with liquid-glass UI; preference remembered |

---

## Architecture

```
┌──────────────────────────┐        REST /api/v1         ┌───────────────────────────┐
│ Frontend (Next.js 15)    │ ──────────────────────────▶ │ Backend (FastAPI)          │
│ landing · app screens    │                              │ engines · services · auth  │
│ R3F ruby-N scene, GSAP   │ ◀────────────────────────── │                            │
└──────────────────────────┘          JSON               └─────────────┬─────────────┘
                                                                        │
                    ┌───────────────────┬──────────────────┬───────────┴──────────┐
                    ▼                   ▼                  ▼                      ▼
             PostgreSQL /          Redis (cache,      Celery worker +       Upstox · AMFI ·
             TimescaleDB           rate limit,        beat (nightly         yfinance ·
             (users, prices)       OAuth state)       price/NAV refresh)    Alpha Vantage
```

User flow:
```
Landing → Register → Deep Profile → Filter → Select & allocate
        → Overlap → PLOS → Risk → ARISE retirement projection
```

---

## Tech stack

**Backend** — Python 3.12, FastAPI, SQLAlchemy 2 (async) + Alembic, PostgreSQL /
TimescaleDB, Redis, Celery, NumPy / SciPy / pandas, Docker Compose.

**Frontend** — Next.js 15 (App Router), React 19, TypeScript, Tailwind CSS v4,
TanStack Query, Zustand, GSAP + ScrollTrigger, Lenis, Motion, three.js /
React Three Fiber / drei, ogl.

---

## Getting started

### Prerequisites
- Docker Desktop
- Python 3.12 (for running tests / scripts outside Docker)
- Node.js 20+ and npm

### 1. Clone
```bash
git clone https://github.com/JINAYSHAH-888/nexus-portfolio-intelligence.git
cd nexus-portfolio-intelligence
```

### 2. Backend
```bash
cd backend
cp .env.example .env          # then fill in values (see Configuration)
docker compose up -d --build  # db, redis, api, worker, beat
docker compose exec api alembic upgrade head
```
API: http://localhost:8000 · interactive docs: http://localhost:8000/docs

Optional — seed a local demo investor with a ready-made portfolio:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python scripts/seed_dev_user.py
```

### 3. Frontend
```bash
cd ../frontend
npm install
echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1" > .env.local
npm run dev -- -p 3400
```
App: http://localhost:3400

> The frontend port must be listed in the backend's `CORS_ORIGINS`
> (3000, 3100 and 3400 are allowed by default), otherwise the browser blocks
> every API call and the app reports "could not reach the backend".

Without `NEXT_PUBLIC_API_BASE_URL` the frontend runs in **demo mode** on bundled
sample data, with a visible "Sample data" badge.

---

## Configuration

All backend settings live in `backend/.env` (template: `backend/.env.example`).
**Never commit `.env`** — it holds real API keys and is git-ignored.

| Variable | Purpose |
|---|---|
| `DATABASE_URL`, `REDIS_URL` | Postgres / Redis connections |
| `SECRET_KEY` | JWT signing key — change for any real deployment |
| `CORS_ORIGINS` | Comma-separated frontend origins allowed to call the API |
| `ALPHA_VANTAGE_API_KEY` | Optional fundamentals source |
| `MARKET_DATA_FORCE_SYNTHETIC` | `true` = use calibrated synthetic prices (offline dev) |
| `MODEL_CONSERVATISM_HAIRCUT` | Explicit return haircut (default 1.25 pp/yr), reported in results |
| `ARISE_DEFAULT_SIMULATIONS` | Monte Carlo paths per projection (default 1,000) |
| `UPSTOX_ENABLED`, `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_REDIRECT_URI` | Upstox OAuth |

---

## Upstox integration

1. Create an app at <https://account.upstox.com/developer/apps> with Redirect URL
   **exactly** `http://localhost:8000/callback`.
2. Put the key/secret in `backend/.env`, set `UPSTOX_ENABLED=true`, restart the API.
3. Sign in to Nexus, then call `GET /api/v1/auth/upstox/login` (e.g. from `/docs`)
   and open the returned `authorize_url` in a browser.
4. Log in on Upstox; you are redirected to `/callback`, which links the account.
5. Check `GET /api/v1/auth/upstox/status` → `"token_valid_now": true`.

Upstox tokens expire daily at **03:30 IST** — repeat steps 3–4 each day.
Then trigger `POST /api/v1/market/tasks/download-prices` for real price history.

---

## Analytics engines

All engines live in `backend/backend/engines/` and are pure, DB-independent and
unit-tested.

- **Overlap** — `|A∩B|/|A∪B|` and Σ min(wₐ, w_b) on normalised factsheet holdings.
- **PLOS** — 10 weighted layers: real return correlation (30%), sector return
  correlation, direct/top-10/weighted stock overlap, sector cosine, category &
  benchmark, risk proximity, fundamental quality, valuation bucket.
- **Risk** — Ledoit–Wolf shrinkage, Student-t (ν = 5) Monte Carlo, Cornish–Fisher
  VaR with Kupiec POF backtest, information ratio, up/down capture, HRP.
- **ARISE v4** — Student-t AR(1) returns anchored on a shrunk & capped CAGR;
  Gompertz mortality to age 105; robotic / guardrails / VPW / ratchet strategies
  on common random numbers; LTCG/STCG tax; India-specific safe-withdrawal context.
  - **Estimation risk:** each simulated lifetime draws its own expected return
    with sd = σ / √(years of history + 10), so short or synthetic histories cannot
    claim certainty. Success is reported as a Jeffreys-posterior estimate with a
    95% range — never "100%".
  - **Conservatism ledger:** every cautious adjustment (haircut, shrinkage,
    estimation risk, mortality) is listed with its size in every result.

---

## Frontend

- **Landing page** — one continuous scroll in five movements with a persistent
  3D ruby "N" (React Three Fiber, transmission material, orbiting shards) that
  travels through scroll stations and keeps moving at rest; pinned GSAP beats,
  scroll-converging headings, data tickers, cursor glow, electric logo.
- **Themes** — light (off-white `#F5F2ED`) and dark (black `#0B0B0B`) with
  crimson accents; Apple-style liquid glass nav, cards and buttons. Landing
  defaults to dark, app screens to light, until the visitor chooses.
- **App screens** — dashboard, schemes, scheme detail, overlap, PLOS, risk,
  ARISE, profile.
- Design contract: [`DESIGN.md`](DESIGN.md). Colour tokens are generated by
  `frontend/scripts/build-tokens.mjs`, which fails unless every text/surface
  pairing passes WCAG 2.2 AA in both themes.
- Landing statistics are generated and verified against the engine by
  `frontend/scripts/build-overlap-dataset.mjs`.

---

## Testing & quality

`make ci` runs everything below. `make help` lists all commands.

```bash
# backend (in-memory SQLite, no Docker needed)
cd backend && python -m pytest tests/ -q

# frontend
cd frontend && npx tsc --noEmit && npm run lint && npm run build

# regenerate verified data / tokens
node frontend/scripts/build-overlap-dataset.mjs
node frontend/scripts/build-tokens.mjs
```

---

## Project structure

```
.
├── backend/
│   ├── backend/
│   │   ├── api/v1/          REST routes (auth, profile, schemes, analysis, market, upstox…)
│   │   ├── engines/         overlap, PLOS, risk, ARISE, filter
│   │   ├── analytics/       risk metrics, HRP, activeness, portfolio statistics
│   │   ├── data/            scheme metadata, factsheet holdings, CPI history
│   │   ├── data_loaders/    AMFI / yfinance / Alpha Vantage fallback chain
│   │   ├── integrations/    Upstox OAuth + market feed
│   │   ├── models/ schemas/ services/ repositories/ tasks/
│   │   └── main.py
│   ├── alembic/  tests/  scripts/
│   └── docker-compose.yml
├── frontend/
│   ├── app/                 Next.js routes (landing, (auth), (app))
│   ├── components/          landing/, app/, scene/, motion/, theme/, brand/, vendor/
│   ├── lib/                 API client, types, scroll/theme/gem stores, generated facts
│   ├── styles/tokens/       generated colour, type, space, motion tokens
│   ├── public/              3D model, data, icons
│   └── scripts/             data + token generators
└── DESIGN.md
```

---

## Data & honesty principles

- Every figure traces to a factsheet disclosure, a live market source, or a
  clearly labelled estimate — nothing is fabricated.
- Synthetic data is always labelled as synthetic on screen.
- Model conservatism is an explicit, named, reported input — never a hidden bias.

---

## Documentation

- [DEMO.md](DEMO.md) — ten-minute walkthrough
- [docs/](docs/README.md) — architecture, API reference, engines, data sources, frontend, deployment, Upstox, ADRs
- [CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) · [CHANGELOG.md](CHANGELOG.md)

---

## Disclaimer

Mutual fund investments are subject to market risks, read all scheme related
documents carefully. Nexus Portfolio Intelligence is independent analysis
software, **not affiliated with, endorsed by, or sponsored by Nippon Life India
Asset Management Limited or AMFI**. Results are model estimates, not guarantees
of future returns, and **not investment advice**.

UI motion adapted from [Skiper UI](https://skiper-ui.com) and
[React Bits](https://reactbits.dev).
