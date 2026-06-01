"""Infrastructure configuration.

Per REQ-009 only *infrastructure* settings are read from the environment:

* ``DATABASE_URL`` — PostgreSQL connection string.
* ``KAFKA_BOOTSTRAP_SERVERS`` — Kafka/Redpanda brokers.
* ``ENCRYPTION_KEY`` — Fernet key used to encrypt API secrets at rest.

Everything else (API keys, strategy parameters, risk limits, app behaviour) lives
in the database and is configured from the frontend. This module therefore stays
intentionally small.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process-level infrastructure settings sourced from the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Infrastructure (the only things allowed to come from env) ---
    database_url: str = Field(
        default="postgresql+asyncpg://bitunix:bitunix@localhost:5432/bitunix",
        description="Async SQLAlchemy PostgreSQL DSN.",
    )
    kafka_bootstrap_servers: str = Field(
        default="localhost:9092",
        description="Comma separated Kafka/Redpanda bootstrap servers.",
    )
    encryption_key: str = Field(
        default="",
        description=(
            "Fernet key (urlsafe base64, 32 bytes) used to encrypt API secrets. "
            "If empty a deterministic dev key is derived; never do that in prod."
        ),
    )

    # --- Local, non-secret operational toggles ---
    app_env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=False, description="Emit JSON logs (prod) vs console (dev).")

    # Kafka topic names (stable defaults; rarely changed).
    kafka_topic_prefix: str = Field(default="bitunix")

    @property
    def sync_database_url(self) -> str:
        """Synchronous DSN (used by Alembic migrations)."""
        return self.database_url.replace("+asyncpg", "+psycopg").replace(
            "postgresql+psycopg", "postgresql"
        )


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    return Settings()
