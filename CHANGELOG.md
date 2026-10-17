# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added — models (see docs/ml-models.md)
- Real market history downloader: verified AMFI NAVs (34 schemes), 188 holding stocks, sector/macro series, Nifty OHLC, official NSE valuation history.
- PLOS v3: holdings-implied correlation and sector-style layers, learned Fisher-z weights, exact Shapley contributions; forward-in-time R² 0.570 → 0.626.
- Risk v2: HAR-X volatility (India VIX, S&P 500, USD/INR, crude), GARCH blend, EVT tail at 99%; QLIKE −20% vs GARCH (DM p < 1e-5).
- ARISE v5: φ = 0, volatility blended with the long run, dividend-yield prior; 80% ranges now hold 82% of real outcomes (was 67%).
- Shared scoring rules (CRPS, PIT, QLIKE, FZ0, Diebold–Mariano), and ADR 0007.

### Fixed
- PLOS v1/v2 counted bond issuers as stock overlap with equity funds.
- PLOS top-5 hard-coded 0.0 overlap for portfolios of five or fewer schemes.


### Added
- Repo tooling: root `Makefile`, `compose.yaml` (full stack incl. web), frontend `Dockerfile`, `.editorconfig`.
- GitHub: frontend CI, secret guard, Dependabot, PR and issue templates.
- `docs/`: architecture, API reference (47 operations), engines, data sources, frontend, deployment, Upstox, ADRs 0001–0006.
- `DEMO.md`, `CONTRIBUTING.md`, `SECURITY.md`.

### Fixed
- Backend CI moved to the root `.github/workflows/` (it never ran from `backend/.github`) and its proto check now uses `nippon_market_data_feed_v3.proto`.

## [0.1.0] — 2026-10

### Added
- FastAPI backend: 35 real Nippon India schemes, Filter / Overlap / PLOS / Risk / ARISE v4 engines, transaction ledger, Upstox OAuth.
- ARISE estimation risk: per-path mean draws, Jeffreys interval, never 0% / 100%.
- Next.js frontend: continuous-scroll landing with the ruby N scene, liquid-glass UI, light/dark themes, dashboard.
