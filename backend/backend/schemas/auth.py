"""
Auth Pydantic schemas (request/response contracts for auth endpoints).
Strict validation at the API boundary — nothing invalid gets to the service layer.
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(min_length=1, max_length=255)

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        """
        Enforce minimal password complexity:
          - At least 8 characters (already covered by Field min_length)
          - At least one digit or special character
        """
        has_digit = any(c.isdigit() for c in v)
        has_special = any(not c.isalnum() for c in v)
        if not (has_digit or has_special):
            raise ValueError(
                "Password must contain at least one digit or special character."
            )
        return v


class LoginRequest(BaseModel):
    """Used internally; the actual endpoint uses OAuth2PasswordRequestForm."""
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenPairResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RegisterResponse(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class MeResponse(BaseModel):
    user_id: uuid.UUID
    email: EmailStr
    role: str
    is_active: bool
