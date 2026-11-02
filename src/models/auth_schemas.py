"""Pydantic schemas for authentication request/response bodies (US-13)."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class RegisterResponse(BaseModel):
    message: str
    user_id: str | None


class VerifyEmailRequest(BaseModel):
    token: str


class ResendVerificationRequest(BaseModel):
    """US-08: Request body for resending verification email."""

    email: EmailStr


class DeviceResponse(BaseModel):
    """US-45: Single active device / session entry returned by GET /api/auth/devices."""

    id: str
    device_type: str
    browser: str | None
    os: str | None
    ip_address: str | None
    location_city: str | None
    location_country: str | None
    created_at: str
    expires_at: str
    is_current: bool
