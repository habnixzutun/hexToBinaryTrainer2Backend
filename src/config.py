"""US-10: Configuration with SMTP support.

Extends Settings with SMTP email configuration for production use.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings with SMTP support.

    Reads from environment variables and .env file.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database settings
    db_name: str = "hexToBinaryTrainer2"
    db_pw: str = ""
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = ""

    # CORS settings
    allowed_origins: list[str] = ["http://localhost:5173"]

    # SMTP settings (US-10)
    smtp_host: str = "smtp.strato.de"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""

    @property
    def async_database_url(self) -> str:
        """Async database URL for SQLAlchemy."""
        return f"postgresql+asyncpg://{self.db_user}:{self.db_pw}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def sync_database_url(self) -> str:
        """Sync database URL for SQLAlchemy."""
        return f"postgresql://{self.db_user}:{self.db_pw}@{self.db_host}:{self.db_port}/{self.db_name}"


@lru_cache
def get_settings() -> Settings:
    """Factory function for Settings with caching.

    Returns a cached Settings instance, avoiding repeated env file reads.
    Use get_settings.cache_clear() to reset the cache (useful for tests).
    """
    return Settings()
