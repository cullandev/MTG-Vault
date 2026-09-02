"""Auth request/response models."""

from __future__ import annotations

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    """Body of ``POST /api/auth/login``."""

    password: str = Field(min_length=1, max_length=1024)


class ChangePasswordRequest(BaseModel):
    """Body of ``POST /api/auth/password``."""

    current: str = Field(min_length=1, max_length=1024)
    new: str = Field(min_length=8, max_length=1024)


class SessionInfo(BaseModel):
    """Response of ``GET /api/auth/session``."""

    authenticated: bool
    expires_at: str | None = None
