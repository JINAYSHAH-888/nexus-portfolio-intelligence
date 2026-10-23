# Nexus Portfolio Intelligence — common tasks. Run `make help`.
.DEFAULT_GOAL := help
FRONTEND_PORT ?= 3400

.PHONY: help setup up down logs dev-backend dev-frontend seed test lint check build data tokens ci stack

help: ## List commands
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n",$$1,$$2}'

setup: ## Install backend (venv) and frontend dependencies
	cd backend && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
	cd frontend && npm install
	@test -f backend/.env || (cp backend/.env.example backend/.env && echo "Created backend/.env — fill in your keys")
	@test -f frontend/.env.local || (echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1" > frontend/.env.local)

up: ## Start backend services (db, redis, api, worker, beat) and migrate
	cd backend && docker compose up -d --build api worker beat
	cd backend && docker compose exec api alembic upgrade head

down: ## Stop backend services
	cd backend && docker compose down

logs: ## Tail API logs
	cd backend && docker compose logs -f api

stack: ## Run EVERYTHING (backend + frontend) in Docker
	docker compose up -d --build

dev-backend: ## Run the API locally with reload (needs db/redis from `make up`)
	cd backend && .venv/bin/uvicorn backend.main:app --reload --loop asyncio --port 8000

dev-frontend: ## Run the frontend dev server
	cd frontend && npm run dev -- -p $(FRONTEND_PORT)

seed: ## Create the local demo investor with a ready-made portfolio
	cd backend && .venv/bin/python scripts/seed_dev_user.py

test: ## Backend test suite
	cd backend && ENVIRONMENT=test MARKET_DATA_FORCE_SYNTHETIC=true REDIS_ENABLED=false .venv/bin/python -m pytest tests/ -q

lint: ## Lint backend and frontend
	cd backend && .venv/bin/ruff check backend/ tests/ scripts/
	cd frontend && npm run lint

check: ## Frontend typecheck + lint
	cd frontend && npx tsc --noEmit && npm run lint

build: ## Frontend production build
	cd frontend && npm run build

data: ## Regenerate verified landing facts from factsheet holdings
	node frontend/scripts/build-overlap-dataset.mjs

tokens: ## Regenerate colour tokens (fails unless WCAG AA passes)
	node frontend/scripts/build-tokens.mjs

ci: lint test check data tokens ## Everything CI runs, locally
