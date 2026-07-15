from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Plex
    plex_server_url: str = "http://10.0.0.30:32400"
    plex_admin_token: str = ""

    # Tautulli
    tautulli_url: str = "http://localhost:8181"
    tautulli_api_key: str = ""

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_model: str = "anthropic/claude-sonnet-4"

    # Database
    database_url: str = ""

    # Application
    secret_key: str = ""
    admin_password: str = ""

    # Scheduler
    enable_scheduler: bool = True
    recommendation_hour: int = 3
    recommendation_minute: int = 0
    playlist_size: int = 15
    allowed_libraries: str = ""  # Comma-separated list of library IDs

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
