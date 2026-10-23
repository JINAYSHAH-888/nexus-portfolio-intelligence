# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

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
