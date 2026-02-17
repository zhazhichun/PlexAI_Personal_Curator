from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Plex
    plex_server_url: str = "http://localhost:32400"
    plex_admin_token: str = ""

    # Tautulli
    tautulli_url: str = "http://localhost:8181"
    tautulli_api_key: str = ""

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4"

    # Database
    database_url: str = "postgresql+asyncpg://plexai:plexai_secret@db:5432/plexai"

    # Application
    secret_key: str = "change-this-to-a-random-secret-key"
    admin_password: str = "admin"

    # Scheduler
    recommendation_hour: int = 3
    recommendation_minute: int = 0
    playlist_size: int = 15

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
