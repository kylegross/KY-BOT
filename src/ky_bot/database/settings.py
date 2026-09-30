"""Async SQLite storage with a versioned schema and guild-scoped atomic writes."""

from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiosqlite


@dataclass(frozen=True, slots=True)
class GuildSettings:
    guild_id: int
    log_channel_id: int | None = None


class SettingsRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def get(self, guild_id: int) -> GuildSettings:
        async with self.connection.execute(
            "SELECT log_channel_id FROM guild_settings WHERE guild_id = ?", (str(guild_id),)
        ) as cursor:
            row = await cursor.fetchone()
        return GuildSettings(guild_id, int(row[0]) if row and row[0] else None)

    async def set_log_channel(self, guild_id: int, channel_id: int) -> None:
        async with self.connection.execute(
            "INSERT INTO guild_settings (guild_id, log_channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET log_channel_id = excluded.log_channel_id",
            (str(guild_id), str(channel_id)),
        ):
            pass

    async def clear_log_channel(self, guild_id: int) -> None:
        # This is the only setting in schema v1, so clearing it removes the saved row.
        async with self.connection.execute(
            "DELETE FROM guild_settings WHERE guild_id = ?", (str(guild_id),)
        ):
            pass


@asynccontextmanager
async def open_settings(path: Path):
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(path), isolation_level=None, timeout=10) as connection:
        async with connection.execute("PRAGMA user_version") as cursor:
            version = (await cursor.fetchone())[0]
        if version > 1:
            raise RuntimeError("Database schema is newer than this version of KY BOT.")
        if version == 0:
            await connection.executescript(
                "BEGIN IMMEDIATE;"
                "CREATE TABLE IF NOT EXISTS guild_settings ("
                "guild_id TEXT PRIMARY KEY, log_channel_id TEXT NOT NULL);"
                "PRAGMA user_version = 1;"
                "COMMIT;"
            )
        yield SettingsRepository(connection)
