from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_name: str = "hexToBinaryTrainer2"
    db_pw: str = ""
    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = ""
    allowed_origins: list[str] = ["http://localhost:5173"]

    @property
    def async_database_url(self) -> str:
        return f"postgresql+asyncpg://{self.db_user}:{self.db_pw}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def sync_database_url(self) -> str:
        return f"postgresql://{self.db_user}:{self.db_pw}@{self.db_host}:{self.db_port}/{self.db_name}"


@lru_cache
def get_settings() -> Settings:
    """Factory function for Settings with caching.

    Returns a cached Settings instance, avoiding repeated env file reads.
    Use get_settings.cache_clear() to reset the cache (useful for tests).
    """
    return Settings()
