"""Celery application."""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from backend.core.config import settings

celery_app = Celery(
    "nippon",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["backend.tasks.market_tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",  # NSE/AMFI operate on IST; schedules below are IST
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30 * 60,
    task_soft_time_limit=25 * 60,
    # These jobs hammer external APIs. Prefetching a big batch into one worker
    # just serialises them behind each other and makes a stuck download block
    # its queued siblings.
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
)

celery_app.conf.beat_schedule = {
    # After the NSE close (15:30 IST) + settlement, so the daily bar is final.
    "download-prices-nightly": {
        "task": "market.download_prices",
        "schedule": crontab(hour=18, minute=30, day_of_week="mon-fri"),
    },
    # AMFI publishes NAVs late evening IST.
    "download-nav-nightly": {
        "task": "market.download_nav",
        "schedule": crontab(hour=23, minute=0),
    },
    # Factsheet holdings are disclosed monthly.
    "refresh-holdings-monthly": {
        "task": "market.refresh_holdings",
        "schedule": crontab(hour=2, minute=0, day_of_month="5"),
    },
    # Upstox refreshes its BOD instrument master ~06:00 IST daily; 06:30 gives
    # that time to land before we re-sync. Hard-fails (see the task's own
    # docstring) rather than silently serving a stale or wrong ISIN map.
    "refresh-upstox-isin-map-daily": {
        "task": "market.refresh_upstox_isin_map",
        "schedule": crontab(hour=6, minute=30),
    },
}
