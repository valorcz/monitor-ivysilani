from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Config(BaseSettings):
    # --- Shared ---
    LOG_LEVEL: str = Field(default="INFO")
    DB_PATH: str = Field(default="episodes.duckdb")

    # --- Discord ---
    DISCORD_BOT_TOKEN: str = Field(default="")
    DATA_DIR: str = Field(default="./data")
    DOWNLOAD_DIR: str = Field(default="./downloads")

    # --- Networking ---
    HTTP_TIMEOUT: int = Field(default=10)
    USER_AGENT: str = Field(
        default="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    )

    # --- Allowed URL pattern (ČT only, overridable by env) ---
    ALLOWED_URL_RE: str = Field(
        default=r"^https://(www\.)?ceskatelevize\.cz/porady/.*"
    )

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

# Singleton-like accessor
CONFIG = Config()