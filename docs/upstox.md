# Upstox integration

Upstox supplies live and historical NSE prices when an account is connected. Without it the app falls back to yfinance — nothing breaks.

## Setup

1. Create an app at the Upstox developer console. Set the redirect URI to `http://localhost:8000/api/v1/auth/upstox/callback` (or whatever `UPSTOX_REDIRECT_URI` says — they must match exactly).
2. Put the API key, secret and redirect URI in `backend/.env`, then `docker compose up -d --build api`.

## Connect

1. Log in to the app, then call `GET /api/v1/auth/upstox/login` — it returns an `authorize_url`.
2. Open it and complete the Upstox login (mobile, OTP, PIN) yourself.
3. Upstox redirects to the callback; the backend exchanges the code and stores the token.
4. Check with `GET /api/v1/auth/upstox/status`.

Tokens **expire daily at 03:30 IST** — reconnect each day. `POST /api/v1/auth/upstox/disconnect` revokes it.

## After connecting

```bash
POST /api/v1/market/tasks/refresh-upstox-isin-map
POST /api/v1/market/tasks/download-prices
```

The `feed-worker` service streams quotes (protobuf `nippon_market_data_feed_v3.proto`) to Redis, served over `WS /api/v1/ws/live-prices`.
