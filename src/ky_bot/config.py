"""Validated environment configuration; environment values override .env."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """A safe-to-display configuration error (never includes secret values)."""


@dataclass(frozen=True, slots=True)
class Settings:
    token: str = field(repr=False)
    command_sync: str = "none"
    dev_guild_id: int | None = None
    log_level: str = "INFO"
    log_dir: Path = Path("logs")
    database_path: Path = Path("data/ky-bot.sqlite3")

    @classmethod
    def from_env(cls, env_file: Path = Path(".env")) -> "Settings":
        load_dotenv(env_file, override=False)
        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token or token.lower() in {"your_token_here", "paste_token_here"}:
            raise ConfigurationError("Set DISCORD_TOKEN in your local .env file.")
        mode = os.getenv("COMMAND_SYNC", "none").strip().lower()
        if mode not in {"none", "guild", "global"}:
            raise ConfigurationError("COMMAND_SYNC must be none, guild, or global.")
        raw_id = os.getenv("DEV_GUILD_ID", "").strip()
        guild_id = None
        if raw_id:
            try:
                guild_id = int(raw_id)
            except ValueError:
                raise ConfigurationError("DEV_GUILD_ID must be a positive server ID.") from None
            if guild_id <= 0 or guild_id >= 2**64:
                raise ConfigurationError("DEV_GUILD_ID must be a positive server ID.")
        if mode == "guild" and guild_id is None:
            raise ConfigurationError("Guild sync requires DEV_GUILD_ID.")
        level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL.")
        database_path = os.getenv("DATABASE_PATH", "data/ky-bot.sqlite3").strip()
        if not database_path:
            raise ConfigurationError("DATABASE_PATH must be a database file path.")
        return cls(
            token, mode, guild_id, level, Path(os.getenv("LOG_DIR", "logs")), Path(database_path)
        )
