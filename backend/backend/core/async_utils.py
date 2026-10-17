"""
Sync -> async bridge.

HARD CONTRACT: `run_async_safely` is imported by pre-existing engine code:
    engines/risk_engine.py            -> fetch scheme returns
    analytics/portfolio_statistics.py -> fetch scheme returns

Those modules are deliberately synchronous (pure, unit-testable, no I/O in
their signatures), but the market-data loaders beneath them are `async def`.
This helper is the seam between the two worlds.

Why it isn't just `asyncio.run(coro)`
-------------------------------------
`asyncio.run()` raises RuntimeError if a loop is already running in the
current thread. Three distinct call contexts have to work:

  1. Celery worker / CLI / pytest sync test  -> no loop anywhere. asyncio.run
     is correct and cheapest.
  2. FastAPI route that offloaded the engine to a worker thread (the normal
     path in this app -- see api/v1/analysis.py, which uses
     `anyio.to_thread.run_sync` so a 5,000-path Monte Carlo never blocks the
     event loop). The worker thread has NO running loop of its own, so we
     again land in case 1 -- but the *main* thread's loop is alive and must
     not be touched.
  3. Someone calls a sync engine directly from inside a coroutine (no thread
     offload). Here a loop IS running in this thread, and blocking it would
     deadlock the server. We hand the coroutine to a private, dedicated
     worker thread with its own fresh event loop and block the *calling*
     thread until it finishes.

Why httpcore is imported eagerly below
--------------------------------------
Hardening, NOT the fix for the segfault described under `_run_in_fresh_loop`
-- that was uvloop, and this import was tried first and did not resolve it.
It is kept because it is independently worth doing: `httpx` defers
`import httpcore` until a transport is constructed, so without this the
import machinery runs on a worker thread on the first outbound request of
every such path. Resolving it here, once, on the main thread makes that a
dict lookup instead.

Case 3 still blocks the calling coroutine (that is unavoidable -- the caller
is synchronous and wants a value), but it does NOT deadlock, and it never
runs two loops in one thread. Routes should prefer the case-2 pattern.
"""
from __future__ import annotations

# Eagerly resolve httpx's deferred transport dependency on the MAIN thread.
# See "Why httpcore is imported eagerly below" in the module docstring: left
# lazy, this import runs inside a worker thread's nested event loop and
# intermittently segfaults the process. Guarded so a missing optional
# dependency can never break importing this module.
try:  # pragma: no cover - import-time warm-up, not branching logic
    import httpcore  # noqa: F401
    import httpx  # noqa: F401
except Exception:  # pragma: no cover
    pass

import asyncio
import contextlib
import logging
from collections.abc import Coroutine
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# A single, lazily-created thread used only for case 3. Kept module-level so we
# don't spin up (and leak) a fresh thread + loop on every nested call.
_bridge_executor: ThreadPoolExecutor | None = None


def _get_bridge_executor() -> ThreadPoolExecutor:
    global _bridge_executor
    if _bridge_executor is None:
        _bridge_executor = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="async-bridge"
        )
    return _bridge_executor


def run_async_safely(coro: Coroutine[Any, Any, T], timeout: float | None = 120.0) -> T:
    """
    Run `coro` to completion from synchronous code and return its result.

    Safe to call whether or not an event loop is already running in the
    current thread.

    Args:
        coro: an *already-constructed* coroutine object. Callers in this repo
            pass e.g. `loader.get_scheme_returns_with_fallback(...)`.
        timeout: seconds to wait. Market-data fallback chains do network I/O;
            an unbounded wait here would hang a request forever if a provider
            black-holes the connection. None disables the timeout.

    Raises:
        Whatever `coro` raises, or asyncio.TimeoutError on expiry.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Case 1 / 2: no loop in this thread -> we own it.
        return _run_in_fresh_loop(coro, timeout)

    # Case 3: a loop is already running in THIS thread. Never block it and
    # never nest a second loop inside it -- offload to the bridge thread.
    logger.debug(
        "run_async_safely called from inside a running event loop; "
        "offloading to the bridge thread. Prefer wrapping the engine call in "
        "anyio.to_thread.run_sync at the route layer instead."
    )
    future = _get_bridge_executor().submit(_run_in_fresh_loop, coro, timeout)
    return future.result()


def _run_in_fresh_loop(coro: Coroutine[Any, Any, T], timeout: float | None) -> T:
    """
    Run `coro` on a PRIVATE, explicitly-constructed selector event loop.

    WHY NOT `asyncio.run()`. THIS IS THE FIX FOR A PROCESS-KILLING SEGFAULT.

    `uvicorn[standard]` pulls in uvloop and installs it as the PROCESS-WIDE
    asyncio policy. `asyncio.run()` honours that policy, so every call to this
    helper from an anyio worker thread was constructing -- and then destroying
    -- a uvloop loop on a NON-MAIN thread, once per scheme, per request.
    uvloop does not survive that pattern: the interpreter segfaulted with no
    traceback and no 500, just a dead uvicorn worker and a dropped connection.

    The symptom was maddening to localise because the fault surfaced wherever
    the corrupted process next touched C code -- `ssl.SSLContext.__new__` on
    one request, `socket.getaddrinfo` on another, the import machinery's
    `_path_join` on a third. None of those were the bug; they were debris.
    GET /api/v1/analysis/risk, which reaches the AMFI loader through this
    helper, failed roughly half the time and could fail 6 times out of 6.

    `asyncio.SelectorEventLoop` is the stdlib's pure-Python loop and is
    constructed DIRECTLY, bypassing the installed policy, so the loop this
    thread runs is never uvloop no matter what the server did globally. The
    main thread keeps its uvloop and loses no performance: this path only ever
    runs on worker threads that were already blocking.

    The loop is set as this THREAD's current loop (thread-local, so the
    server's own loop is untouched) because some libraries call
    `asyncio.get_event_loop()` internally, and is always closed afterwards --
    including its async generators -- so repeated calls cannot leak loops.
    """
    loop = asyncio.SelectorEventLoop()
    try:
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_with_timeout(coro, timeout))
    finally:
        # Teardown must never mask the result the caller is waiting for.
        with contextlib.suppress(Exception):  # pragma: no cover
            loop.run_until_complete(loop.shutdown_asyncgens())
        try:
            loop.close()
        finally:
            asyncio.set_event_loop(None)


async def _with_timeout(coro: Coroutine[Any, Any, T], timeout: float | None) -> T:
    if timeout is None:
        return await coro
    return await asyncio.wait_for(coro, timeout=timeout)


def shutdown_bridge() -> None:
    """Called on app shutdown so the interpreter can exit cleanly."""
    global _bridge_executor
    if _bridge_executor is not None:
        _bridge_executor.shutdown(wait=False, cancel_futures=True)
        _bridge_executor = None
