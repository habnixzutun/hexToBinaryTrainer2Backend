"""Pydantic schemas for authentication request/response bodies (US-13)."""

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class RegisterResponse(BaseModel):
    message: str
    user_id: str


class VerifyEmailRequest(BaseModel):
    token: str
