# ── Builder ───────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
# Build wheels once here so the runtime stage needs no compiler at all -- that
# keeps the final image small and drops the whole build-toolchain CVE surface.
RUN pip wheel --wheel-dir /wheels -r requirements.txt


# ── Runtime ───────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

# Never run as root.
RUN useradd --create-home --uid 10001 nippon

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

COPY backend/ ./backend/
COPY alembic/ ./alembic/
COPY alembic.ini scripts/ ./
COPY scripts/ ./scripts/

RUN mkdir -p /app/var/reports && chown -R nippon:nippon /app
USER nippon

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://localhost:8000/health || exit 1

# --loop asyncio is LOad-BEARING, not a preference. `uvicorn[standard]` pulls
# in uvloop and uses it by default. This app routinely offloads the Monte
# Carlo engines to worker threads (anyio.to_thread.run_sync) and those threads
# run their own short-lived event loops for async market-data loaders
# (core/async_utils.run_async_safely). Under uvloop that pattern corrupts the
# process: GET /api/v1/analysis/risk segfaulted the uvicorn worker on roughly
# half of all requests -- no traceback, no 500, just a dropped connection and
# "Child process died". The fault surfaced wherever the damaged process next
# entered C code (ssl.SSLContext.__new__, socket.getaddrinfo, the import
# machinery), which is why it looked like three unrelated bugs.
#
# Measured on a cold cache: uvloop 0/8 requests succeeded, asyncio 8/8.
# uvloop's advantage is raw socket throughput, which is not this service's
# bottleneck -- its cost centre is CPU-bound simulation already running off
# the loop -- so this trade trends strongly positive.
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000", \
     "--workers", "4", "--loop", "asyncio"]
