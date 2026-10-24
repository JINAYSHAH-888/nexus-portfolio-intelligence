<div align="center">

# Nexus Portfolio Intelligence

**Mutual fund overlap, risk and retirement analytics for Indian investors, built on 35 real Nippon India schemes and models proven on data they never saw.**

![Python](https://img.shields.io/badge/Python-3.12-13110f?style=flat-square)
![FastAPI](https://img.shields.io/badge/FastAPI-backend-13110f?style=flat-square)
![Next.js](https://img.shields.io/badge/Next.js-15-13110f?style=flat-square)
![Tests](https://img.shields.io/badge/tests-449_passing-d41828?style=flat-square)
![Models](https://img.shields.io/badge/models-validated_out_of_sample-d41828?style=flat-square)

[What it does](#what-it-does) · [The models](#the-models) · [Quick start](#quick-start) · [Architecture](#architecture) · [Docs](#documentation)

</div>

---

> Most portfolios of five or six funds quietly hold the same stocks twice.
> Nexus shows you exactly where, what it costs your diversification, and what it means for your retirement.

**A real finding (factsheet data, 31 May 2026):** HDFC Bank, ICICI Bank, Axis Bank and SBI each sit in **11 of Nippon India's 20 equity funds**. Large Cap and Quant share only 15 holdings, yet **41% of their money** is in the same stocks. On the dev portfolio, four funds behave like **1.24 independent funds**.

---

## What it does

| | |
|---|---|
| 🧭 **Deep profile** | A 10-field investor profile drives every analysis |
| 🔎 **Scheme filter** | All 35 schemes stay visible; mismatches are *flagged with reasons*, never hidden |
| 🔗 **Overlap** | Shared names (Jaccard) **and** shared money (Σ min weight), pair by pair |
| 🧮 **PLOS v3** | Predicts how strongly two funds will *move together*, with every layer's contribution shown exactly |
| 📉 **Risk v2** | VaR and ES that track today's volatility, using India VIX and global markets |
| 🏖️ **ARISE v5** | Retirement Monte Carlo whose ranges are calibrated against real outcomes; never shows 100% |
| 📡 **Live data** | Upstox OAuth, AMFI NAVs, NSE prices, with a graceful fallback chain |
| 🌗 **Design** | Cinematic dark landing with a 3D ruby "N"; off-white, red and ink app screens; liquid glass |

---

## The models

Each engine has its **own ML and quant models**. They're trained offline on real, downloaded market history, and **each one ships only if it beats the model it replaces on data it never saw.** Every comparison is shown on the relevant screen. Full evidence: [`docs/ml-models.md`](docs/ml-models.md).

### Data behind them

- **34 schemes' daily NAVs** (AMFI, 2013–). Every AMFI code is verified against the NAV printed on the scheme's own factsheet.
- **188 holding stocks.** Prices cover 92–100% of each Indian fund's disclosed weight.
- **Factor series:** 10 Nifty sector indices, India VIX, S&P 500, USD/INR, crude and gold.
- **Valuation and history:** official NSE Nifty P/E, P/B and dividend yield since 1999, plus 29 years of Sensex.

### PLOS v3: overlap that predicts co-movement

New quant layers:
- **Holdings-implied correlation:** today's weights × a Ledoit–Wolf shrunk covariance of the stocks held.
- **Sector style similarity:** Sharpe style analysis on 9 sector indices.

Model:
- Weights are learned in Fisher-z space over 7 yearly origins (2,994 pair-years), then shrunk toward the original hand weights.
- Per-layer contributions are **exact Shapley values**.

| Model | Forward-in-time R² | Fund held out R² |
|---|---|---|
| PLOS v1 (as shipped) | 0.570 | 0.666 |
| **PLOS v3 (used)** | **0.626** | **0.701** |
| Gradient-boosted trees | 0.575 | 0.709 |

*Trees did best cross-sectionally but worst forward in time, so they weren't shipped.*

### Risk v2: volatility that knows what the market knows

**HAR-X** forecasts Nifty volatility from realised volatility plus India VIX, the overnight S&P 500, USD/INR and crude. That forecast is blended with each portfolio's own GARCH and mapped through its beta. VaR uses filtered historical simulation at 95% and an **extreme-value (GPD) tail** at 99%.

| Volatility model (2,400 days, out of sample) | QLIKE, 1 day ↓ | QLIKE, 5 days ↓ |
|---|---|---|
| GARCH-t (previous) | 0.421 | 0.268 |
| **HAR-X (used)** | **0.336** | **0.206** |

That's **20% lower error**, and Diebold–Mariano p < 10⁻⁵, so it isn't luck. On **13 real funds × 21,450 days**, the new VaR/ES improves the joint FZ0 score at both 95% and 99%, and passes the breach-count test for every fund.

### ARISE v5: ranges you can trust

ARISE was replayed on every Indian equity fund since 2016, forecasting three years ahead with only the data available at the time:

| | Outcomes inside the "80%" range | Inside the "50%" range |
|---|---|---|
| ARISE v4 | 67.4%: overconfident | 37.4% |
| **ARISE v5** | **82.8%** | **47.2%** |

What changed:
- **No assumed mean reversion.** 29 years of Sensex returns can't tell it from noise.
- **Volatility blended with its long-run level.** A calm stretch no longer makes the projection overconfident.
- **A Nifty dividend-yield valuation prior.** It beat the historical mean out of sample (R² +0.45) and is shrunk for small samples.
- **A regime-switching model** runs as a labelled crash-clustering stress test.

Other carry-overs:
- **Estimation risk:** each lifetime draws its own expected return.
- **Probabilities:** reported as a Jeffreys estimate with a 95% range, never 0% or 100%.
- **Tax:** Indian rules apply (LTCG ₹1.25 lakh exemption, 12.5%).

> **What was tested and rejected:** gradient-boosted trees (PLOS), a boosted volatility model (Risk), regime-switching as the main model, a joint equity–inflation bootstrap (no link in Indian data, r = −0.15), and Meucci's minimum-torsion ENB. The reasons are in [ADR 0007](docs/adr/0007-models-chosen-out-of-sample.md).

---

## Quick start

**Prerequisites:** Docker Desktop, Python 3.12, Node.js 20+.

```bash
git clone https://github.com/JINAYSHAH-888/nexus-portfolio-intelligence.git
cd nexus-portfolio-intelligence
make setup      # backend venv + frontend deps; creates backend/.env from the template
make stack      # everything in Docker: web :3400, api :8000, db, redis, workers
docker compose exec api alembic upgrade head
make seed       # a local demo investor with a ready-made portfolio
```

Then open:
- App: **http://localhost:3400**
- API docs: **http://localhost:8000/docs**

Prefer running the frontend outside Docker? Use `make up` (backend only), then `make dev-frontend`. `make help` lists every command.

**Refresh the data and retrain all models** (about 8 minutes):

```bash
make fetch-market && make train-models
```

> The frontend port must be in the backend's `CORS_ORIGINS` (3000, 3100 and 3400 are allowed by default). Without `NEXT_PUBLIC_API_BASE_URL`, the frontend runs in **demo mode** on sample data, with a visible badge.

---

## Architecture

```
┌──────────────────────────┐     REST /api/v1      ┌──────────────────────────────┐
│ Frontend  (Next.js 15)   │ ────────────────────▶ │ Backend  (FastAPI)            │
│ landing · app screens    │                       │ engines · services · auth     │
│ R3F ruby-N, GSAP, Lenis  │ ◀──────────────────── │ ml/ reads trained artifacts   │
└──────────────────────────┘        JSON           └───────────────┬──────────────┘
                                                                    │
      ┌──────────────────┬──────────────────┬───────────────────┬──┴────────────────┐
      ▼                  ▼                  ▼                   ▼                   ▼
 PostgreSQL /        Redis (cache,      Celery worker      Upstox · AMFI ·    scripts/train_models.py
 TimescaleDB         rate limit,        + beat             NSE · Yahoo        → data/models/*.json
                     OAuth state)
```

**User flow:** Landing → Register → Deep Profile → Filter → Select & allocate → Overlap → PLOS → Risk → ARISE.

**Tech:**
- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2 + Alembic, PostgreSQL/TimescaleDB, Redis, Celery, NumPy/SciPy/pandas, scikit-learn.
- **Frontend:** Next.js 15, React 19, TypeScript, Tailwind v4, TanStack Query, GSAP + ScrollTrigger, Lenis, Motion, three.js / React Three Fiber.

<details>
<summary><b>Project structure</b></summary>

```
.
├── backend/
│   ├── backend/
│   │   ├── api/v1/          REST routes (auth, profile, schemes, analysis, market, upstox…)
│   │   ├── engines/         overlap, PLOS, risk, ARISE, filter
│   │   ├── ml/              PLOS v3, Risk v2, ARISE v5 models + scoring rules
│   │   ├── analytics/       risk metrics, HRP, portfolio statistics
│   │   ├── data/            schemes, holdings, CPI · market_history/ · models/
│   │   ├── data_loaders/    AMFI / yfinance / Alpha Vantage fallback chain
│   │   ├── integrations/    Upstox OAuth + market feed
│   │   └── models/ schemas/ services/ repositories/ tasks/
│   ├── scripts/             fetch_market_history.py · train_models.py · seed_dev_user.py
│   ├── alembic/  tests/
│   └── docker-compose.yml
├── frontend/
│   ├── app/                 landing, (auth), (app) routes
│   ├── components/          landing/, app/, scene/, theme/, brand/, vendor/
│   ├── lib/  styles/tokens/  scripts/
├── docs/                    architecture, API, engines, ML models, ADRs …
├── .github/                 CI (backend, frontend, secret guard), Dependabot, templates
├── compose.yaml  Makefile  DESIGN.md
```
</details>

<details>
<summary><b>Configuration</b> (<code>backend/.env</code>, never committed)</summary>

| Variable | Purpose |
|---|---|
| `DATABASE_URL`, `REDIS_URL` | Postgres / Redis connections |
| `SECRET_KEY` | JWT signing key; change it for any real deployment |
| `CORS_ORIGINS` | Frontend origins allowed to call the API |
| `UPSTOX_ENABLED`, `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_REDIRECT_URI` | Upstox OAuth ([guide](docs/upstox.md)) |
| `ALPHA_VANTAGE_API_KEY` | Optional fundamentals source |
| `MARKET_DATA_FORCE_SYNTHETIC` | `true` = calibrated synthetic prices (offline dev; PLOS then falls back to its v1 scoring) |
| `MODEL_CONSERVATISM_HAIRCUT` | Explicit, reported return haircut |
| `ARISE_DEFAULT_SIMULATIONS` | Monte Carlo paths per projection (default 1,000) |
</details>

---

## Quality

- **449 backend tests**, including the models: GARCH recovers known parameters, EVT matches the normal tail, Shapley contributions sum exactly, PLOS can never fall as overlap rises, and bond issuers are never counted as stock overlap.
- **CI on every push:** ruff and pytest; tsc, ESLint and build; checks that regenerated data and tokens match what's committed; and a guard that fails if any `.env` is committed.
- **Generated, gated data:** landing figures reproduce the engine's own numbers, and colour tokens must pass 16 WCAG AA checks.

```bash
make ci        # everything CI runs, locally
```

---

## Principles

1. **Nothing is fabricated.** Every figure traces to a factsheet, a market source, or a clearly labelled estimate.
2. **Never claim certainty.** No probability shows as 0% or 100%; small samples get shrunk.
3. **Conservatism is named, not hidden.** Every cautious adjustment is reported with its size.
4. **Simple until proven otherwise.** A model replaces another only by winning out of sample.

---

## Documentation

| | |
|---|---|
| [DEMO.md](DEMO.md) | Ten-minute walkthrough |
| [docs/ml-models.md](docs/ml-models.md) | Every model, its data, and its out-of-sample evidence |
| [docs/](docs/README.md) | Architecture · [API (47 endpoints)](docs/api.md) · engines · data sources · frontend · deployment · Upstox |
| [docs/adr/](docs/adr/README.md) | Seven architecture decision records |
| [CONTRIBUTING](CONTRIBUTING.md) · [SECURITY](SECURITY.md) · [CHANGELOG](CHANGELOG.md) | Project policies |

---

## Disclaimer

Mutual fund investments are subject to market risks; read all scheme-related documents carefully. Nexus Portfolio Intelligence is independent analysis software, **not affiliated with, endorsed by, or sponsored by Nippon Life India Asset Management Limited or AMFI**. All results are model estimates, not guarantees of future returns, and **not investment advice**.

UI motion adapted from [Skiper UI](https://skiper-ui.com) and [React Bits](https://reactbits.dev).
