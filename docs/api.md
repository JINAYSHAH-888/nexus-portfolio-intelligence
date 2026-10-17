# API reference

Generated from the live OpenAPI schema (`/openapi.json`). Base URL: `http://localhost:8000`. Interactive docs: `/docs`.

Auth: `POST /api/v1/auth/login` takes **OAuth2 form data** (`username`, `password`), not JSON, and returns access + refresh tokens. Send `Authorization: Bearer <access>`.

Analysis endpoints additionally require a completed profile and at least one selected scheme (404 `Scheme selection not found` otherwise).

## health

| Method | Path | Summary | Auth |
|---|---|---|---|
| GET | `/health` | Health | — |
| GET | `/health/ready` | Ready | — |

## meta

| Method | Path | Summary | Auth |
|---|---|---|---|
| GET | `/api/v1/meta/assumptions` | Get Assumptions | — |

## auth

| Method | Path | Summary | Auth |
|---|---|---|---|
| POST | `/api/v1/auth/register` | Register | — |
| POST | `/api/v1/auth/login` | Login | — |
| POST | `/api/v1/auth/refresh` | Refresh | — |
| POST | `/api/v1/auth/logout` | Logout | — |
| POST | `/api/v1/auth/logout-all` | Logout All | yes |
| GET | `/api/v1/auth/me` | Me | yes |

## upstox

| Method | Path | Summary | Auth |
|---|---|---|---|
| GET | `/api/v1/auth/upstox/login` | Login | yes |
| GET | `/api/v1/auth/upstox/callback` | Callback | — |
| GET | `/api/v1/auth/upstox/status` | Status | yes |
| POST | `/api/v1/auth/upstox/disconnect` | Disconnect | yes |

## profile

| Method | Path | Summary | Auth |
|---|---|---|---|
| GET | `/api/v1/profile` | Read Profile | yes |
| PUT | `/api/v1/profile` | Update Profile | yes |
| POST | `/api/v1/profile` | Create Profile | yes |

## schemes

| Method | Path | Summary | Auth |
|---|---|---|---|
| GET | `/api/v1/schemes/filtered` | Filtered | yes |
| POST | `/api/v1/schemes/select` | Select | yes |
| POST | `/api/v1/schemes/allocate` | Allocate | yes |
| GET | `/api/v1/schemes/selected` | Selected | yes |
| GET | `/api/v1/schemes/{scheme_id}` | Scheme Detail | yes |

## analysis

| Method | Path | Summary | Auth |
|---|---|---|---|
| GET | `/api/v1/analysis/overlap` | Overlap | yes |
| GET | `/api/v1/analysis/plos` | Plos | yes |
| GET | `/api/v1/analysis/risk` | Risk | yes |
| POST | `/api/v1/analysis/arise` | Arise | yes |
| GET | `/api/v1/analysis/prices` | Prices | yes |
| POST | `/api/v1/analysis/compare` | Compare | yes |

## portfolio

| Method | Path | Summary | Auth |
|---|---|---|---|
| POST | `/api/v1/portfolio/transactions` | Create Transaction | yes |
| GET | `/api/v1/portfolio/transactions` | List Transactions | yes |
| DELETE | `/api/v1/portfolio/transactions/{txn_id}` | Delete Transaction | yes |
| GET | `/api/v1/portfolio/pnl` | Pnl | yes |
| GET | `/api/v1/portfolio/holdings` | Holdings | yes |

## market

| Method | Path | Summary | Auth |
|---|---|---|---|
| GET | `/api/v1/market/portfolio-value` | Portfolio Value | yes |
| GET | `/api/v1/market/dashboard` | Dashboard | yes |
| GET | `/api/v1/market/pnl` | Pnl | yes |
| GET | `/api/v1/market/fundamentals/{ticker}` | Fundamentals | yes |
| GET | `/api/v1/market/nav/{scheme_id}` | Nav | yes |
| POST | `/api/v1/market/tasks/download-prices` | Trigger Download Prices | yes |
| POST | `/api/v1/market/tasks/download-nav` | Trigger Download Nav | yes |
| POST | `/api/v1/market/tasks/refresh-holdings` | Trigger Refresh Holdings | yes |
| POST | `/api/v1/market/tasks/refresh-upstox-isin-map` | Trigger Refresh Upstox Isin Map | yes |

## reports

| Method | Path | Summary | Auth |
|---|---|---|---|
| POST | `/api/v1/reports/generate` | Generate | yes |
| GET | `/api/v1/reports` | List Reports | yes |
| GET | `/api/v1/reports/{report_id}` | Get Report | yes |
| GET | `/api/v1/reports/{report_id}/download` | Download | yes |

## notifications

| Method | Path | Summary | Auth |
|---|---|---|---|
| GET | `/api/v1/notifications/mine` | Mine | yes |
| POST | `/api/v1/notifications/{notification_id}/read` | Mark Read | yes |

Plus WebSocket `/api/v1/ws/live-prices` (not in OpenAPI).

_47 HTTP operations._
