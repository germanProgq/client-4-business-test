from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "approval-service"
    environment: str = "local"
    log_level: str = "INFO"

    # Async DSN (asyncpg) used by the running application.
    database_url: str = "postgresql+asyncpg://approval:approval@localhost:5432/approval_service"

    # Sync DSN (psycopg2) used only by Alembic migrations. Defaults to the
    # async URL with the driver swapped, so a single DATABASE_URL env var
    # is enough for the common case.
    database_url_sync: Optional[str] = None

    outbox_poll_interval_seconds: float = 2.0

    # Pagination guardrails for list endpoints.
    default_page_size: int = 20
    max_page_size: int = 100

    def resolved_sync_database_url(self) -> str:
        if self.database_url_sync:
            return self.database_url_sync
        return self.database_url.replace("postgresql+asyncpg", "postgresql+psycopg2")


@lru_cache
def get_settings() -> Settings:
    return Settings()
