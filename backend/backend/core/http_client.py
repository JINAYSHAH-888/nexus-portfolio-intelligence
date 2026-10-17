"""
One process-wide TLS context, built on the main thread at import.

WHY THIS EXISTS
---------------
`httpx.AsyncClient(...)` builds a BRAND NEW `ssl.SSLContext` every time it is
constructed, and each one parses the entire CA bundle from disk. This service
constructs a client per outbound call, several per request, so it was paying
that cost continuously for no benefit.

An honest note on provenance: this module was written while chasing a
process-killing segfault whose faulthandler trace pointed at
`ssl.SSLContext.__new__`. Sharing the context did NOT fix that crash -- the
real cause was uvloop, see `core.async_utils._run_in_fresh_loop` -- and the
SSL frame turned out to be debris from an already-corrupted process rather
than the bug. It is kept purely on its own merits: one context, built once,
is both faster and the documented way to use SSLContext, which is explicitly
designed to be shared across threads and connections.

USAGE: pass `verify=SSL_CONTEXT` to every httpx client constructed anywhere
that might run inside a worker thread.
"""
from __future__ import annotations

import logging
import ssl
from typing import Any

logger = logging.getLogger(__name__)


def _build_default_context() -> Any:
    """
    Prefer httpx's own context so verification behaviour is identical to the
    default it would otherwise construct per client; fall back to the stdlib
    default if httpx's internals move.
    """
    try:
        import httpx

        return httpx.create_ssl_context()
    except Exception:  # pragma: no cover - fallback for httpx API drift
        try:
            import certifi

            return ssl.create_default_context(cafile=certifi.where())
        except Exception:
            return ssl.create_default_context()


# Built at import, on the main thread, exactly once per process.
SSL_CONTEXT = _build_default_context()
