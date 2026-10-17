# Deployment

## Full stack with Docker Compose

```bash
cp backend/.env.example backend/.env   # fill in SECRET_KEY etc.
make stack                             # docker compose up -d --build (all services)
docker compose exec api alembic upgrade head
```

Backend only (then run the web app with `make dev-frontend`):

```bash
make up                                # api, worker, beat + migrations
```

- Web: http://localhost:3400 · API: http://localhost:8000 · Docs: http://localhost:8000/docs
- The API image bakes in the code (no volume mount): after backend changes, `docker compose up -d --build api`.
- `NEXT_PUBLIC_API_BASE_URL` is a **build arg** of the web image — changing it needs a rebuild.

## Environment (`backend/.env`)

| Variable | Notes |
|---|---|
| `DATABASE_URL` | Postgres/Timescale connection string |
| `SECRET_KEY` | JWT signing key — must be changed for any real deployment |
| `REDIS_URL`, `REDIS_ENABLED` | Rate limiting falls back to in-process if Redis is down |
| `CORS_ORIGINS` | Must include the frontend origin (e.g. `http://localhost:3400`) |
| `UPSTOX_ENABLED`, `UPSTOX_API_KEY`, `UPSTOX_API_SECRET`, `UPSTOX_REDIRECT_URI` | See [upstox.md](upstox.md) |
| `ALPHA_VANTAGE_API_KEY` | Optional |
| `ARISE_DEFAULT_SIMULATIONS`, `ARISE_MAX_SIMULATIONS` | Monte Carlo sizing |

`.env` files are git-ignored and the `secret-guard` workflow fails if one is ever tracked.

## Migrations

Alembic, in `backend/alembic/`. `0001_initial`, `0002_transactions_and_sip`, `0003_upstox_connections`. Generate new ones with `alembic revision --autogenerate` and review them by hand.

## CI (GitHub Actions)

| Workflow | Runs on | Does |
|---|---|---|
| `backend-ci.yml` | `backend/**` | ruff, metadata `--check`, proto regeneration check, pytest |
| `frontend-ci.yml` | `frontend/**` | npm ci, regenerate data + tokens and `git diff --exit-code`, tsc, lint, build |
| `secret-guard.yml` | every push / PR | fails if a non-example `.env` is tracked |

Dependabot updates pip, npm, Actions and Docker weekly.

## Before a public launch

- Add the "not affiliated with Nippon Life India AMC / AMFI; not investment advice" disclaimer to the site.
- Add Skiper UI attribution (or confirm a Pro licence).
- Rotate `SECRET_KEY` and any API keys used in development.
