"""
Password hashing and JWT issuance/verification.

Design notes:
  - Access and refresh tokens are both JWTs but carry a `type` claim and are
    verified against the expected type. Without that check, an attacker could
    present a long-lived refresh token as an access token (or vice versa) --
    a classic confused-deputy bug.
  - Every token carries a `jti`. For refresh tokens that jti is the handle we
    revoke on logout (see models.RefreshToken).
  - bcrypt is used directly rather than through passlib, whose bcrypt backend
    is broken against bcrypt>=4.1 (it introspects `bcrypt.__about__`, which
    was removed). Using the library directly avoids that whole class of
    version-coupling breakage.
  - bcrypt silently truncates inputs at 72 BYTES. RegisterRequest allows 128
    chars, and a long UTF-8 passphrase can exceed 72 bytes well before 72
    chars -- so we pre-hash with SHA-256 to a fixed-length digest. This makes
    the full password contribute entropy instead of being cut off mid-string.
"""
from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt
from jose import JWTError, jwt

from backend.core.config import settings

TokenType = Literal["access", "refresh"]


class TokenError(Exception):
    """Raised when a token is malformed, expired, or of the wrong type."""


# ── Passwords ─────────────────────────────────────────────────────────────────
def _prehash(password: str) -> bytes:
    """
    SHA-256 -> base64, so the value handed to bcrypt is always 44 bytes and
    never hits bcrypt's 72-byte truncation. Standard practice (Dropbox's
    scheme); base64 keeps it free of NUL bytes, which bcrypt also truncates on.
    """
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt(rounds=settings.BCRYPT_ROUNDS)
    return bcrypt.hashpw(_prehash(password), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(plain), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # Malformed hash in the DB -> auth failure, not a 500.
        return False


# ── Tokens ────────────────────────────────────────────────────────────────────
def _create_token(
    subject: str,
    token_type: TokenType,
    expires_delta: timedelta,
    extra: dict[str, Any] | None = None,
) -> tuple[str, str, datetime]:
    """Returns (encoded_jwt, jti, expires_at)."""
    now = datetime.now(UTC)
    expires_at = now + expires_delta
    jti = uuid.uuid4().hex

    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
        "nbf": int(now.timestamp()),
    }
    if extra:
        payload.update(extra)

    encoded = jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded, jti, expires_at


def create_access_token(user_id: uuid.UUID | str, role: str = "investor") -> str:
    token, _, _ = _create_token(
        str(user_id),
        "access",
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        extra={"role": role},
    )
    return token


def create_refresh_token(user_id: uuid.UUID | str) -> tuple[str, str, datetime]:
    """Returns (token, jti, expires_at) -- the caller persists jti+expiry."""
    return _create_token(
        str(user_id),
        "refresh",
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """
    Decode and validate a JWT.

    Raises TokenError on a bad signature, expiry, or -- importantly -- a
    type mismatch, so a refresh token can never be replayed as an access token.
    """
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError as e:
        raise TokenError(f"Invalid or expired token: {e}") from e

    actual = payload.get("type")
    if actual != expected_type:
        raise TokenError(
            f"Wrong token type: expected '{expected_type}', got '{actual}'."
        )
    if not payload.get("sub"):
        raise TokenError("Token is missing its subject claim.")
    if not payload.get("jti"):
        raise TokenError("Token is missing its jti claim.")
    return payload
