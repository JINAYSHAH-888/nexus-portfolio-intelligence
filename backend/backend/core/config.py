"""
Central application settings.

This module is a HARD CONTRACT: the pre-existing data_loaders/analytics code
imports `settings` and reads specific attributes. Grepped usages that must
keep working verbatim:

    settings.DATABASE_URL           analytics/duckdb_client.py
    settings.ALPHA_VANTAGE_API_KEY  data_loaders/alpha_vantage_loader.py
    settings.AMFI_BASE_URL          data_loaders/amfi_loader.py
    settings.AMFI_NAV_REFRESH_DAYS  data_loaders/amfi_loader.py

Do not rename those four without updating their call sites.
"""
from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import computed_field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Repo root = .../backend/core/config.py -> parents[2]
ROOT_DIR = Path(__file__).resolve().parents[2]
BACKEND_DIR = ROOT_DIR / "backend"
DATA_DIR = BACKEND_DIR / "data"

PLACEHOLDER_SECRET = "CHANGE_ME_IN_PRODUCTION"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # ── App ────────────────────────────────────────────────────────────────────
    APP_NAME: str = "Nexus Portfolio Intelligence"
    API_V1_PREFIX: str = "/api/v1"
    ENVIRONMENT: Literal["development", "test", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    # ── Database ───────────────────────────────────────────────────────────────
    # Async driver for the app; duckdb_client rewrites this to a sync psycopg DSN.
    DATABASE_URL: str = "postgresql+asyncpg://nippon:nippon@localhost:5432/nippon"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False

    # ── Auth ───────────────────────────────────────────────────────────────────
    SECRET_KEY: str = PLACEHOLDER_SECRET
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    BCRYPT_ROUNDS: int = 12

    # ── Redis ──────────────────────────────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_ENABLED: bool = True

    # ── CORS ───────────────────────────────────────────────────────────────────
    # NoDecode: pydantic-settings normally tries its OWN json.loads() on any
    # env var feeding a list-typed field, BEFORE our field_validator below
    # ever runs. That silently broke the documented .env.example format
    # (CORS_ORIGINS=http://a,http://b) with a raw SettingsError -- caught by
    # actually booting the app against a real .env-style value, not just
    # unit tests that construct Settings() with a Python list directly.
    # NoDecode defers all parsing to our own validator.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = ["http://localhost:3000", "http://localhost:5173"]

    # ── Rate limiting ──────────────────────────────────────────────────────────
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_DEFAULT_PER_MINUTE: int = 120
    RATE_LIMIT_AUTH_PER_MINUTE: int = 10
    # ARISE is the most expensive endpoint in the system (n_simulations x
    # n_years x 4 strategies). It gets its own, much tighter bucket.
    RATE_LIMIT_ANALYSIS_PER_MINUTE: int = 12
    # PDF generation runs the full overlap+PLOS+risk stack, then renders. Tight.
    RATE_LIMIT_REPORTS_PER_MINUTE: int = 6

    # ── Market data ────────────────────────────────────────────────────────────
    ALPHA_VANTAGE_API_KEY: str | None = None
    AMFI_BASE_URL: str = "https://api.mfapi.in"
    AMFI_NAV_REFRESH_DAYS: int = 1
    MARKET_DATA_FORCE_SYNTHETIC: bool = False
    # Explicit operator override for the risk-free rate. Leave unset (None) and
    # the system derives it from the overnight fund's own DISCLOSED YTM -- real
    # factsheet data rather than a constant that goes stale the moment the RBI
    # moves. See core/assumptions.py::resolve_risk_free_rate.
    RISK_FREE_RATE: float | None = None
    PRICE_PERIOD: str = "3y"
    STATS_PRICE_PERIOD: str = "10y"

    # ── Model conservatism ─────────────────────────────────────────────────────
    # A DELIBERATE, NAMED deduction from the expected return that both the risk
    # engine's forward simulation and ARISE's retirement projection apply.
    #
    # This is NOT a maths correction. Both engines previously ran ~sigma^2/2
    # pessimistic by ACCIDENT -- the risk engine applied an Ito correction to a
    # drift that was already logarithmic, and ARISE fed a geometric CAGR into a
    # process that adds its own volatility drag (the error Kitces and Boldin
    # both describe; Voyant calls it a "double-correction"). Those are now
    # fixed, so the models deliver the CAGR they claim.
    #
    # Removing an accidental bias should not silently make every retirement
    # projection rosier, so the conservatism is re-applied here as an explicit
    # margin an operator can see, justify and change. Responses report the
    # return both before and after it, so the deduction is never hidden.
    #
    # 0.0 disables it and gives the statistically unbiased model.
    #
    # 125bps is a DELIBERATE, OPERATOR-CONFIRMED calibration, not a placeholder
    # and not a derived quantity. It was chosen to reproduce the level of
    # conservatism the engines had by accident before the return-space bugs
    # were fixed (measured: -1.19pp/yr realised, against -1.38pp/yr before),
    # so correcting the maths did not make every existing retirement plan
    # suddenly look rosier. Do not "tidy it away" as a magic number: changing
    # it rescales every projection this platform produces, so it is a product
    # decision, not a refactor.
    MODEL_CONSERVATISM_HAIRCUT: float = 0.0125

    # ── ARISE ──────────────────────────────────────────────────────────────────
    ARISE_DEFAULT_SIMULATIONS: int = 1000
    ARISE_MAX_SIMULATIONS: int = 5000
    ARISE_SEED: int | None = 42

    # ── Upstox ─────────────────────────────────────────────────────────────────
    # Broker integration: real equity/index prices, live holdings, MF identity
    # and tradability. Does NOT replace AMFI (Upstox's MF file has no NAV
    # history, only a single BOD snapshot) or the AMC factsheet pipeline (no
    # portfolio holdings). See backend/integrations/upstox/README or
    # core/assumptions.py for what each source is and is not responsible for.
    UPSTOX_API_KEY: str | None = None
    UPSTOX_API_SECRET: str | None = None
    # MUST match EXACTLY what's registered in the Upstox Developer App
    # dashboard -- Upstox validates redirect_uri against that registration
    # and rejects a mismatch with "Invalid Credentials" (their own docs).
    # The app is registered with the bare path below, NOT
    # /api/v1/auth/upstox/callback -- see main.py's top-level /callback
    # route, which is what actually listens here.
    UPSTOX_REDIRECT_URI: str = "http://localhost:8000/callback"
    UPSTOX_ENABLED: bool = False  # explicit opt-in; absence of creds must not 500

    # The dedicated Upstox login the feed worker connects as -- NEVER an
    # individual investor's personal OAuth connection. See
    # integrations/upstox/feed_worker.py's module docstring for why: one
    # upstream socket serves every investor, so it cannot be tied to any one
    # of their personal accounts disconnecting.
    UPSTOX_SERVICE_ACCOUNT_USER_ID: str | None = None

    UPSTOX_BASE_URL: str = "https://api.upstox.com/v2"
    UPSTOX_WS_AUTHORIZE_URL: str = "https://api.upstox.com/v3/feed/market-data-feed/authorize"
    UPSTOX_PORTFOLIO_WS_AUTHORIZE_URL: str = "https://api.upstox.com/v3/feed/portfolio-stream-feed/authorize"

    # BOD instrument master files. Refreshed by Upstox ~06:00 IST daily; we
    # re-download on the same cadence rather than trusting a stale local copy.
    UPSTOX_INSTRUMENTS_NSE_URL: str = (
        "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
    )
    UPSTOX_INSTRUMENTS_MF_URL: str = (
        "https://assets.upstox.com/market-quote/instruments/exchange/mf-instruments.json.gz"
    )
    UPSTOX_INSTRUMENTS_GLOBAL_URL: str = (
        "https://assets.upstox.com/market-quote/instruments/exchange/global.json.gz"
    )
    ISIN_MAP_PATH: Path = DATA_DIR / "upstox_isin_map.json"
    EQUITY_MAP_PATH: Path = DATA_DIR / "upstox_equity_map.json"

    # Benchmark indices streamed alongside every subscription -- confirmed real
    # instrument_keys from the Upstox Global/NSE instrument master, not guessed.
    # Same NoDecode fix as CORS_ORIGINS below -- an env override here would
    # otherwise hit the identical pydantic-settings JSON-decode-before-validator
    # crash if ever set as a plain comma list.
    UPSTOX_BENCHMARK_INSTRUMENT_KEYS: Annotated[list[str], NoDecode] = [
        "NSE_INDEX|Nifty 50",
        "NSE_INDEX|Nifty Bank",
    ]

    @field_validator("UPSTOX_BENCHMARK_INSTRUMENT_KEYS", mode="before")
    @classmethod
    def _split_benchmark_keys(cls, v: object) -> object:
        if isinstance(v, str):
            return [k.strip() for k in v.split(",") if k.strip()]
        return v

    # V3 feed subscription caps (Appendix, "Normal Connection and Subscription
    # Limits"). Individual = max instrument keys for ONE mode; the worker
    # shards a subscription batch against whichever mode it's using.
    UPSTOX_WS_LIMIT_LTPC: int = 5000
    UPSTOX_WS_LIMIT_FULL: int = 2000
    UPSTOX_WS_LIMIT_OPTION_GREEKS: int = 3000

    # ── Celery ─────────────────────────────────────────────────────────────────
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"
    CELERY_TASK_ALWAYS_EAGER: bool = False

    # ── Reports ────────────────────────────────────────────────────────────────
    REPORTS_DIR: Path = ROOT_DIR / "var" / "reports"

    # ── Data files ─────────────────────────────────────────────────────────────
    SCHEMES_PATH: Path = DATA_DIR / "schemes_metadata.json"
    HOLDINGS_PATH: Path = DATA_DIR / "sample_holdings.json"
    TICKER_MAP_PATH: Path = DATA_DIR / "stocks_ticker_map.json"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        # With NoDecode, v arrives as the RAW env string (or, when Settings()
        # is constructed directly in Python -- e.g. every test in this repo --
        # as an already-real list, which must pass through unchanged).
        if isinstance(v, str):
            s = v.strip()
            if s.startswith("["):
                import json

                return json.loads(s)
            return [o.strip() for o in s.split(",") if o.strip()]
        return v

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_database_url(self) -> str:
        """Sync DSN for Alembic and the DuckDB/Postgres analytics scanner."""
        return self.DATABASE_URL.replace("+asyncpg", "+psycopg2").replace("+aiosqlite", "")

    def validate_production_safety(self) -> list[str]:
        """
        Fail-fast checks applied at startup in production.

        Returned as a list (rather than raising on the first) so an operator
        sees every misconfiguration at once instead of fixing them one deploy
        at a time.
        """
        problems: list[str] = []
        if not self.is_production:
            return problems
        if self.SECRET_KEY == PLACEHOLDER_SECRET or len(self.SECRET_KEY) < 32:
            problems.append(
                "SECRET_KEY is the placeholder or shorter than 32 chars. "
                "Generate one with: python -c 'import secrets;print(secrets.token_urlsafe(48))'"
            )
        if "sqlite" in self.DATABASE_URL:
            problems.append("DATABASE_URL points at SQLite in production; use Postgres.")
        if "*" in self.CORS_ORIGINS:
            problems.append("CORS_ORIGINS contains '*' in production.")
        if self.MARKET_DATA_FORCE_SYNTHETIC:
            problems.append(
                "MARKET_DATA_FORCE_SYNTHETIC=true in production would serve "
                "simulated returns to real investors."
            )
        return problems


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()


def generate_secret_key() -> str:
    return secrets.token_urlsafe(48)
