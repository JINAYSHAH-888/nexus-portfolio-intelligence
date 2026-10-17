.PHONY: install dev test lint migrate up down normalize

install:
	pip install -r requirements-dev.txt

dev:
	uvicorn backend.main:app --reload --port 8000

test:
	ENVIRONMENT=test MARKET_DATA_FORCE_SYNTHETIC=true REDIS_ENABLED=false pytest tests/ -q

lint:
	ruff check backend/ tests/ scripts/

migrate:
	alembic upgrade head

normalize:
	python scripts/normalize_scheme_metadata.py

up:
	docker compose up -d --build
	docker compose exec api alembic upgrade head

down:
	docker compose down
