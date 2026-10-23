# Demo walkthrough

About ten minutes, local only.

## 1. Start

```bash
make setup     # backend venv + frontend npm install
make stack     # full stack in Docker (web :3400, api :8000)
docker compose exec api alembic upgrade head
make seed      # local demo investor with a ready-made portfolio
```

Or run only the backend in Docker with `make up`, then `make dev-frontend`.

## 2. Landing — http://localhost:3400

Scroll the whole page. The ruby N travels with you through five movements:
- **Opening** — four banks sit in 11 of Nippon India's 20 equity funds.
- **Evidence** (pinned) — Large Cap × Quant: 15 shared names, 41.07% shared money.
- **Engines** — overlap, PLOS, risk, ARISE.
- **Ledger** (pinned) — unbiased return → conservatism haircut → after haircut.
- **Close** — sign up.

Toggle light/dark in the nav.

## 3. App

Sign in with the seeded account (credentials in `backend/scripts/seed_dev_user.py`).

1. **Dashboard** — four readings: overlap, diversification, risk, retirement.
2. **Profile** — the 10-field deep profile.
3. **Schemes** — all 35 schemes; mismatches are flagged, not hidden.
4. **Analysis** — overlap heatmap and network, PLOS layers, risk (VaR, Kupiec verdict), ARISE.

Note ARISE never shows 100%: success is a Jeffreys estimate with a 95% interval.

## 4. API

http://localhost:8000/docs — every endpoint is in [docs/api.md](docs/api.md).

## 5. Live prices (optional)

Connect Upstox per [docs/upstox.md](docs/upstox.md); without it the app uses yfinance.
