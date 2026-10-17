"""Cross-user isolation and information-leak checks."""
from __future__ import annotations

import uuid

import pytest_asyncio

from tests.conftest import VALID_PROFILE


@pytest_asyncio.fixture
async def other_user(client) -> dict:
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "mallory@example.com", "password": "Str0ngPass1!", "full_name": "M"},
    )
    body = r.json()
    h = {"Authorization": f"Bearer {body['access_token']}"}
    await client.post("/api/v1/profile", json=VALID_PROFILE, headers=h)
    return {"headers": h, "user_id": body["user_id"]}


async def test_profiles_are_per_user(client, profiled, other_user):
    """Mallory must never see Ananya's profile on the shared GET /profile route."""
    r = await client.put(
        "/api/v1/profile", json={"occupation": "Mallory Job"}, headers=other_user["headers"]
    )
    assert r.status_code == 200

    r = await client.get("/api/v1/profile", headers=profiled["headers"])
    assert r.json()["occupation"] == VALID_PROFILE["occupation"]
    assert r.json()["user_id"] == profiled["user_id"]


async def test_selections_are_per_user(client, selected, other_user):
    r = await client.get("/api/v1/schemes/selected", headers=other_user["headers"])
    assert r.status_code == 404  # Mallory has selected nothing

    r = await client.get("/api/v1/schemes/selected", headers=selected["headers"])
    assert r.json()["user_id"] == selected["user_id"]


async def test_cannot_read_another_users_report(client, selected, other_user):
    """
    IDOR guard. A report carries the investor's income, corpus and holdings --
    serving it off a guessable UUID without an owner check would be a direct
    PII leak.
    """
    r = await client.post("/api/v1/reports/generate", headers=selected["headers"])
    assert r.status_code == 201, r.text
    report_id = r.json()["id"]

    for path in (f"/api/v1/reports/{report_id}", f"/api/v1/reports/{report_id}/download"):
        rr = await client.get(path, headers=other_user["headers"])
        assert rr.status_code == 403, f"{path} leaked to another user"

    # And the owner can still read it.
    assert (await client.get(f"/api/v1/reports/{report_id}", headers=selected["headers"])).status_code == 200


async def test_cannot_mark_another_users_notification_read(client, db_session, profiled, other_user):
    from backend.models import Notification

    n = Notification(
        investor_user_id=uuid.UUID(profiled["user_id"]),
        notification_type="rm_alert",
        subject="High overlap detected",
        body="Your portfolio shows 72% overlap.",
    )
    db_session.add(n)
    await db_session.commit()
    await db_session.refresh(n)

    r = await client.post(
        f"/api/v1/notifications/{n.id}/read", headers=other_user["headers"]
    )
    assert r.status_code == 403

    r = await client.post(f"/api/v1/notifications/{n.id}/read", headers=profiled["headers"])
    assert r.status_code == 200
    assert r.json()["is_read"] is True


async def test_notifications_list_is_scoped_to_the_caller(client, db_session, profiled, other_user):
    from backend.models import Notification

    db_session.add(
        Notification(
            investor_user_id=uuid.UUID(profiled["user_id"]),
            notification_type="rm_alert",
            subject="For Ananya only",
            body="private",
        )
    )
    await db_session.commit()

    r = await client.get("/api/v1/notifications/mine", headers=other_user["headers"])
    assert r.json()["count"] == 0

    r = await client.get("/api/v1/notifications/mine", headers=profiled["headers"])
    assert r.json()["count"] == 1
    assert r.json()["unread_count"] == 1
    assert r.json()["notifications"][0]["subject"] == "For Ananya only"


async def test_internal_errors_do_not_leak_tracebacks(client, selected, monkeypatch):
    """A 500 must never hand the client a stack trace, file path, or DSN."""
    from backend.services import analysis_service

    def boom(*_a, **_k):
        raise RuntimeError("psycopg://user:hunter2@10.0.0.5/prod exploded at line 42")

    monkeypatch.setattr(analysis_service, "compute_overlap", boom)

    r = await client.get("/api/v1/analysis/overlap", headers=selected["headers"])
    assert r.status_code == 500
    body = r.json()
    assert body["message"] == "An internal error occurred."
    assert "hunter2" not in r.text
    assert "psycopg" not in r.text
    # The request id is the handle for correlating with the server log.
    assert body["request_id"]


async def test_password_hash_is_never_returned(client, registered):
    r = await client.get("/api/v1/auth/me", headers=registered["headers"])
    assert "password" not in r.text.lower()
    assert "hashed" not in r.text.lower()
