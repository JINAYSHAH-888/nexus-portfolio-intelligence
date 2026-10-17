"""Auth: registration, login, rotation, reuse detection, revocation."""
from __future__ import annotations

import pytest


async def test_register_returns_token_pair(client):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "a@example.com", "password": "Str0ngPass1!", "full_name": "A"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["token_type"] == "bearer"


async def test_duplicate_email_conflicts(client):
    payload = {"email": "dup@example.com", "password": "Str0ngPass1!", "full_name": "D"}
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201
    r = await client.post("/api/v1/auth/register", json=payload)
    assert r.status_code == 409
    assert r.json()["error"] == "conflict"


async def test_email_is_case_insensitive(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "Case@Example.com", "password": "Str0ngPass1!", "full_name": "C"},
    )
    # Registering the same address in a different case must collide, and login
    # with a different case must work -- email local-parts are case-insensitive
    # in every mail system that matters.
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": "case@example.com", "password": "Str0ngPass1!", "full_name": "C"},
    )
    assert r.status_code == 409

    r = await client.post(
        "/api/v1/auth/login",
        data={"username": "CASE@EXAMPLE.COM", "password": "Str0ngPass1!"},
    )
    assert r.status_code == 200


@pytest.mark.parametrize(
    "password,ok",
    [
        ("short1!", False),        # < 8 chars
        ("alllowercase", False),   # no digit, no special
        ("Str0ngPass1!", True),
        ("has-a-special", True),
        ("has1digit", True),
    ],
)
async def test_password_policy(client, password, ok):
    r = await client.post(
        "/api/v1/auth/register",
        json={"email": f"{abs(hash(password))}@e.com", "password": password, "full_name": "P"},
    )
    assert (r.status_code == 201) is ok


async def test_long_password_is_not_truncated_at_72_bytes(client):
    """
    bcrypt silently truncates at 72 BYTES. We SHA-256 pre-hash, so two long
    passwords sharing a 72-byte prefix must NOT be interchangeable.
    """
    base = "A" * 72
    await client.post(
        "/api/v1/auth/register",
        json={"email": "long@e.com", "password": base + "correct-tail-1", "full_name": "L"},
    )
    # Same first 72 bytes, different tail -> must be rejected.
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": "long@e.com", "password": base + "WRONG-tail-9999"},
    )
    assert r.status_code == 401

    r = await client.post(
        "/api/v1/auth/login",
        data={"username": "long@e.com", "password": base + "correct-tail-1"},
    )
    assert r.status_code == 200


async def test_wrong_password_and_unknown_user_are_indistinguishable(client):
    await client.post(
        "/api/v1/auth/register",
        json={"email": "real@e.com", "password": "Str0ngPass1!", "full_name": "R"},
    )
    bad_pw = await client.post(
        "/api/v1/auth/login", data={"username": "real@e.com", "password": "Wr0ngPass!"}
    )
    no_user = await client.post(
        "/api/v1/auth/login", data={"username": "ghost@e.com", "password": "Wr0ngPass!"}
    )
    # Identical status AND identical message -- otherwise the endpoint is an
    # account-enumeration oracle.
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json()["message"] == no_user.json()["message"]


async def test_refresh_rotates_and_burns_the_old_token(client, registered):
    old = registered["refresh"]
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": old})
    assert r.status_code == 200
    new = r.json()["refresh_token"]
    assert new != old

    # The old one is dead now.
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": old})
    assert r.status_code == 401


async def test_refresh_reuse_kills_the_whole_token_family(client, registered):
    """
    OAuth 2.0 BCP replay response: presenting an already-rotated refresh token
    means two parties hold it, so every session for that user is revoked --
    including the one the attacker (or the legitimate client) just obtained.
    """
    old = registered["refresh"]
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": old})
    stolen_successor = r.json()["refresh_token"]

    # Replay the burned token.
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": old})
    assert r.status_code == 401
    assert "all sessions have been ended" in r.json()["message"].lower()

    # The successor issued before the replay is now dead too.
    r = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen_successor})
    assert r.status_code == 401


async def test_access_token_cannot_be_used_as_a_refresh_token(client, registered):
    """The `type` claim check: a short-lived access token must not buy a new pair."""
    r = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": registered["access"]}
    )
    assert r.status_code == 401


async def test_refresh_token_cannot_authenticate_a_request(client, registered):
    r = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {registered['refresh']}"},
    )
    assert r.status_code == 401


async def test_logout_all_revokes_every_session(client, registered):
    # Open a second session.
    r = await client.post(
        "/api/v1/auth/login",
        data={"username": "ananya@example.com", "password": "Str0ngPassw0rd!"},
    )
    second_refresh = r.json()["refresh_token"]

    r = await client.post("/api/v1/auth/logout-all", headers=registered["headers"])
    assert r.status_code == 200
    assert r.json()["sessions_revoked"] == 2

    for tok in (registered["refresh"], second_refresh):
        rr = await client.post("/api/v1/auth/refresh", json={"refresh_token": tok})
        assert rr.status_code == 401


async def test_logout_is_idempotent_and_quiet(client, registered):
    for _ in range(2):
        r = await client.post(
            "/api/v1/auth/logout", json={"refresh_token": registered["refresh"]}
        )
        assert r.status_code == 200
    # Garbage token still 'succeeds' -- nothing to protect, and failing loudly
    # would leak whether a token was ever valid.
    r = await client.post("/api/v1/auth/logout", json={"refresh_token": "not-a-jwt"})
    assert r.status_code == 200


async def test_protected_routes_reject_missing_and_garbage_tokens(client):
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    r = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer garbage.token.here"}
    )
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate") == "Bearer"


async def test_token_signed_with_a_different_key_is_rejected(client, registered):
    from jose import jwt

    forged = jwt.encode(
        {"sub": registered["user_id"], "type": "access", "jti": "x", "exp": 9999999999},
        "attacker-key",
        algorithm="HS256",
    )
    r = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401
