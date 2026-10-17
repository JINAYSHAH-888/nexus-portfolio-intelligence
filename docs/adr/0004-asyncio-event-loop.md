# 0004 — asyncio loop for local development

**Status:** Accepted

## Context
`run_async_safely` and some data loaders interact badly with uvloop when the API runs outside Docker.

## Decision
`make dev-backend` starts uvicorn with `--loop asyncio`. Engine calls still run in worker threads (`anyio.to_thread.run_sync`), where there is no running loop.

## Consequences
Slightly lower throughput in development, no behavioural difference in results.
