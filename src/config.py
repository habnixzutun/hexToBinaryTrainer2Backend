from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_name: str
    db_pw: str
    db_host: str
    db_port: int = 5432
    db_user: str
    allowed_origins: list[str] = ["http://localhost:5173"]

    @property
    def database_url(self) -> str:
        return f"postgresql+asyncpg://{self.db_user}:{self.db_pw}@{self.db_host}:{self.db_port}/{self.db_name}"


settings = Settings()
