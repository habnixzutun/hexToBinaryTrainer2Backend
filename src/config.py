import os
from functools import lru_cache
from typing import Any

from pydantic import SecretStr, ValidationError, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _reveal(secret: SecretStr) -> str:
    """Return the plaintext inside a SecretStr. Internal only -- never log this."""
    return secret.get_secret_value()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=os.environ.get("ENV_FILE"), extra="ignore"
    )

    db_name: str = "hexToBinaryTrainer2"
    db_pw: SecretStr = SecretStr("")
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = ""
    allowed_origins: list[str] = ["http://localhost:5173"]
    cookie_secure: bool = True

    smtp_host: str = "smtp.strato.de"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from_email: str = ""
    base_url: str = "http://localhost:8000"
    frontend_url: str = "http://localhost:5173"

    jwt_secret: SecretStr = SecretStr("")

    # US-07: key used to derive the Fernet key that encrypts email-change
    # tokens. Must be at least 32 chars of entropy so the derived 256-bit
    # key is not trivially brute-forceable. The default value is only safe
    # for the test suite; production deployments MUST set EMAIL_CHANGE_KEY.
    email_change_key: SecretStr = SecretStr(
        "test-email-change-key-must-be-32-chars-min"
    )

    @field_validator("email_change_key")
    @classmethod
    def _validate_email_change_key(cls, value: SecretStr) -> SecretStr:
        if len(value.get_secret_value()) < 32:
            raise ValueError(
                "email_change_key must be at least 32 characters long"
            )
        return value

    @model_validator(mode="after")
    def _validate_smtp(self) -> "Settings":
        if self.smtp_user:
            if not _reveal(self.smtp_password):
                raise ValueError("SMTP config incomplete: smtp_password is required")
            if not self.smtp_from_email:
                raise ValueError("SMTP config incomplete: smtp_from_email is required")
        return self

    @model_validator(mode="after")
    def _validate_jwt(self) -> "Settings":
        secret = _reveal(self.jwt_secret)
        if not secret.strip():
            raise ValueError("JWT_SECRET is required")
        if secret == "dev-secret-change-in-production":
            raise ValueError("JWT_SECRET must not be the old default value")
        if len(secret) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return self

    def __init__(self, **values: Any) -> None:
        """Wrap init so pydantic ValidationErrors never carry plaintext secrets.

        Pydantic v2 stores the raw field values inside ``ValidationError.errors()``
        under the ``input`` key. For ``SecretStr`` fields those raw values are
        still plain strings at this point -- ``SecretStr.__repr__`` only kicks
        in when the *parsed* object is rendered. A validator failure would
        therefore leak ``db_pw`` / ``smtp_password`` / ``jwt_secret`` through
        ``traceback.format_exc()`` and friends. We re-write ``input`` in every
        error dict to the masked ``SecretStr`` form before re-raising.
        """
        try:
            super().__init__(**values)
        except ValidationError as exc:
            secret_fields = {
                name
                for name, field in self.__class__.model_fields.items()
                if field.annotation is SecretStr
            }
            for err in exc.errors():
                raw_input = err.get("input")
                if isinstance(raw_input, dict):
                    for field_name in secret_fields:
                        if field_name in raw_input:
                            raw_input[field_name] = SecretStr(
                                str(raw_input[field_name])
                            )
            raise

    @property
    def async_database_url(self) -> str:
        return f"postgresql+asyncpg://{self.db_user}:{_reveal(self.db_pw)}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def sync_database_url(self) -> str:
        return f"postgresql://{self.db_user}:{_reveal(self.db_pw)}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def jwt_signing_key(self) -> str:
        """Public accessor for the plaintext JWT signing key.

        Keeps callers (e.g. ``jwt_utils``) from reaching into the module-level
        ``_reveal`` helper, which is an internal secret-mask utility.
        """
        return _reveal(self.jwt_secret)


@lru_cache
def get_settings() -> Settings:
    """Factory function for Settings with caching.

    Returns a cached Settings instance, avoiding repeated env file reads.
    Use get_settings.cache_clear() to reset the cache (useful for tests).

    Raises:
        ValueError: On invalid configuration, listing only the offending
            fields and reasons -- never the raw input payload. pydantic
            embeds plaintext env values (db_pw, smtp_password) in
            ValidationError.input_value, which would otherwise reach
            stderr/logs when app startup crashes (PR #26 HIGH finding).
    """
    try:
        return Settings()
    except ValidationError as exc:
        details = "; ".join(
            (
                f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
                if err["loc"]
                else err["msg"]
            )
            for err in exc.errors()
        )
        # from None: suppress the original ValidationError context so its
        # input_value (plaintext secrets) can never be printed in a traceback.
        raise ValueError(f"Invalid configuration: {details}") from None
