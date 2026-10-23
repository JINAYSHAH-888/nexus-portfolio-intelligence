# Architecture

## Services

`compose.yaml` at the repo root includes `backend/docker-compose.yml` and adds the web app.

| Service | Role | Port |
|---|---|---|
| `web` | Next.js 15 frontend (production build) | 3400 |
| `api` | FastAPI app (`backend.main:app`) | 8000 |
| `db` | PostgreSQL + TimescaleDB — users, profiles, selections, ledger, prices | 5432 |
| `redis` | Cache, sliding-window rate limiter, OAuth state, pub/sub for live prices | 6379 |
| `worker` | Celery worker — price / NAV / holdings refresh jobs | — |
| `beat` | Celery beat — nightly schedules | — |
| `feed-worker` | Upstox market-data feed (protobuf v3) → Redis pub/sub | — |

## Backend layering

```
api/v1/        HTTP only: validation, auth dependencies, status codes
services/      business logic; owns transactions; calls engines off the event loop
engines/       pure Python, DB-independent, deterministic given a seed
analytics/     statistics builders feeding the engines (shrinkage lives here)
data_loaders/  external sources with graceful fallback
repositories/  DB access
models/ schemas/  ORM and Pydantic contracts
tasks/         Celery jobs
```

Rules the code relies on:

- Engines are CPU-bound and synchronous; services call them through `anyio.to_thread.run_sync`.
- Allocation weights are **fractions summing to 1.0**, never percentages.
- Non-finite floats are scrubbed to `null` by `core/json.py::SafeJSONResponse` — NaN is not valid JSON.
- Portfolio positions are always replayed from the transaction ledger (FIFO), never stored as a running balance.

## Request flow

```
Register → Deep Profile → /schemes/filtered → /schemes/select → /schemes/allocate
        → /analysis/overlap → /analysis/plos → /analysis/risk → /analysis/arise
```

## Frontend

```
app/page.tsx        landing (server component; client islands for the gem and pinned beats)
app/(auth)/         login, register
app/(app)/          dashboard, profile, schemes, analysis — glass shell + nav
components/         landing/, app/, scene/ (R3F ruby N), theme/, vendor/
lib/                API client, theme, generated tokens and data
scripts/            build-tokens.mjs, build-overlap-dataset.mjs (CI-checked generators)
```

See [frontend.md](frontend.md).
