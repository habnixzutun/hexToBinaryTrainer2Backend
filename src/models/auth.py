"""US-09: Authentication Schema Models.

SQLAlchemy models for the new authentication system supporting:
- Email-based login with username
- Multi-provider authentication (email, OAuth, passkey)
- JWT access/refresh tokens
- Password reset flow
- Audit logging

All tables use UUID7 as primary keys for time-ordering and performance.
"""

from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
)
from sqlalchemy.orm import relationship
from uuid_utils import uuid7

from src.database.engine import Base


class User(Base):
    """User account with email and username.

    Stores core user data. Authentication credentials are in UserIdentity
    to support multiple providers (email, OAuth, passkey).
    """

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    username = Column(String(50), nullable=False, index=True, unique=True)
    email = Column(String(255), nullable=False, index=True, unique=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)
    last_username_change_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    identities = relationship(
        "UserIdentity", back_populates="user", cascade="all, delete-orphan"
    )
    refresh_tokens = relationship(
        "UserRefreshToken", back_populates="user", cascade="all, delete-orphan"
    )
    password_reset_tokens = relationship(
        "PasswordResetToken", back_populates="user", cascade="all, delete-orphan"
    )
    audit_logs = relationship(
        "UserAuditLog", back_populates="user", cascade="all, delete-orphan"
    )


class UserIdentity(Base):
    """Authentication identity for a user.

    Supports multiple authentication providers per user:
    - provider='email', provider_user_id=email, password_hash set
    - provider='google', provider_user_id=google_id, password_hash NULL
    - provider='github', provider_user_id=github_id, password_hash NULL
    - provider='passkey', provider_user_id=credential_id, password_hash NULL

    verified_at is set when email is verified (for email provider).
    """

    __tablename__ = "user_identities"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider = Column(String(50), nullable=False)
    provider_user_id = Column(String(255), nullable=False)
    password_hash = Column(String(255), nullable=True)
    verified_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="identities")

    __table_args__ = (
        Index("ix_user_identities_provider_user", "provider", "provider_user_id"),
    )


class UserRefreshToken(Base):
    """Refresh token for long-lived sessions.

    Opaque tokens stored as hashes. Access tokens are short-lived JWTs (15 min),
    refresh tokens are long-lived (7 days) and can be revoked.
    """

    __tablename__ = "user_refresh_tokens"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = Column(String(255), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="refresh_tokens")


class PasswordResetToken(Base):
    """Token for password reset or email change verification.

    Used for:
    - type='password_reset': Password reset flow
    - type='email_change': Email change verification

    Token is single-use (used_at set after use).
    """

    __tablename__ = "password_reset_tokens"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash = Column(String(255), nullable=False, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)
    type = Column(String(50), nullable=False)
    created_at = Column(DateTime(timezone=True), default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="password_reset_tokens")


class UserAuditLog(Base):
    """Audit log for security-relevant actions.

    Tracks: password_change, password_reset, email_change, username_change
    Includes IP address, user agent, and optional metadata (JSON).
    """

    __tablename__ = "user_audit_log"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid7()))
    user_id = Column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action = Column(String(50), nullable=False)
    timestamp = Column(DateTime(timezone=True), default=datetime.utcnow, index=True)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(Text, nullable=True)
    metadata_json = Column(Text, nullable=True)  # JSON string

    # Relationships
    user = relationship("User", back_populates="audit_logs")

    __table_args__ = (
        Index("ix_user_audit_log_user_timestamp", "user_id", "timestamp"),
    )
