# Contributing

## Setup

```bash
make setup
make up             # db, redis, api, workers in Docker + migrations
make dev-frontend   # http://localhost:3400
```

Run `make help` for every target.

## Before opening a PR

```bash
make ci             # ruff, pytest, tsc, eslint, data + token gates
```

CI runs the same checks (see [docs/deployment.md](docs/deployment.md#ci-github-actions)).

## Ground rules

- **No fabricated numbers.** Every figure traces to a factsheet, a market-data API, or a labelled estimate.
- **Never report certainty.** Probabilities must not render as 0% or 100% ([ADR 0001](docs/adr/0001-estimation-risk.md)).
- **Engines stay pure** — no DB or network inside `backend/backend/engines/`.
- **Weights are fractions** summing to 1.0.
- **Generated files are generated.** Edit the generator, then `make data tokens`.
- **Tokens only** in frontend components — no hex literals.
- **Never commit `.env`.** Only `.env.example`.
- Significant design choices get an ADR in `docs/adr/`.

## Commits

Short imperative subject (`Add`, `Fix`, `Refactor`), body explaining *why*.
