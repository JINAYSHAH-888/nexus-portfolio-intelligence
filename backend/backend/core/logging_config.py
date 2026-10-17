"""Logging setup. Plain text in dev, single-line JSON in production."""
from __future__ import annotations

import json
import logging
import sys

from backend.core.config import settings


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if rid := getattr(record, "request_id", None):
            payload["request_id"] = rid
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging() -> None:
    root = logging.getLogger()
    root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if settings.LOG_JSON or settings.is_production:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-7s %(name)-38s %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    root.addHandler(handler)
    root.setLevel(settings.LOG_LEVEL.upper())

    # yfinance/urllib3 are extremely chatty at INFO, and the market-data layer
    # is *expected* to fail in a no-egress environment -- those warnings are
    # normal operation here, not incidents. Keep them out of the signal.
    for noisy in ("urllib3", "yfinance", "peewee", "asyncio", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
