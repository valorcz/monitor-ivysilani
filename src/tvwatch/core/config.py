from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from pathlib import Path


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

    # --- Allowed URL pattern (ČT only) ---
    ALLOWED_URL_RE: str = Field(default=r"^https://(www\.)?ceskatelevize\.cz/porady/.*")

    # --- Downloader / yt-dlp configuration ---
    # Full executable to invoke (takes precedence if set)
    YTDLP_EXECUTABLE: str | None = Field(default=None)
    # If not set, build from prefix + script name (keeps your patched wrapper)
    YTDLP_PREFIX_DIR: str = Field(default="/yt-dlp")
    YTDLP_SCRIPT_NAME: str = Field(default="yt-dlp.sh")
    # Extra args to prepend (space-separated string), optional
    YTDLP_EXTRA_ARGS: str = Field(default="")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # ---- Derived properties ----
    @property
    def ytdlp_cmd(self) -> list[str]:
        """
        Returns the base command to launch yt-dlp, honoring overrides:
          1) YTDLP_EXECUTABLE (absolute or relative)
          2) YTDLP_PREFIX_DIR + YTDLP_SCRIPT_NAME
        """
        if self.YTDLP_EXECUTABLE:
            exe = self.YTDLP_EXECUTABLE
        else:
            exe = str(Path(self.YTDLP_PREFIX_DIR) / self.YTDLP_SCRIPT_NAME)

        extra = [a for a in self.YTDLP_EXTRA_ARGS.split() if a]
        # If you still want to run via 'uv', you can include it in EXTRA_ARGS, e.g.:
        # YTDLP_EXTRA_ARGS="uv run"
        return [*extra, exe]


CONFIG = Config()
