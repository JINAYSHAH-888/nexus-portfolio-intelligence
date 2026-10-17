"""
Downloads Upstox's BOD instrument master files.

Sync on purpose: this is called from Celery workers, which are sync processes
with no running event loop -- there is nothing to gain from httpx.AsyncClient
here, and using the sync client avoids bridging into asyncio.run() for no
reason (see core/async_utils.py's docstring for why that bridge exists and
when it's actually needed -- this isn't one of those cases).
"""
from __future__ import annotations

import logging

import httpx

from backend.core.http_client import (
    SSL_CONTEXT,  # one shared TLS context; per-client creation segfaults in worker threads
)

logger = logging.getLogger(__name__)


def download_bytes(url: str, *, timeout: float = 30.0) -> bytes:
    """
    Returns the raw response body exactly as Upstox serves it (gzip-compressed
    JSON) -- decompression is the caller's job, not this function's, so a
    caller that wants to persist the raw file to disk unmodified still can.
    """
    with httpx.Client(timeout=timeout, follow_redirects=True, verify=SSL_CONTEXT) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content
