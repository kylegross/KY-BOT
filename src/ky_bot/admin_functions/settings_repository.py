"""Async SQLite storage with a versioned schema and guild-scoped atomic writes."""

import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import aiosqlite

DEFAULT_WELCOME = "Welcome {member} to {server}!"


@dataclass(frozen=True, slots=True)
class GuildSettings:
    guild_id: int
    log_channel_id: int | None = None
    welcome_channel_id: int | None = None
    welcome_message: str = DEFAULT_WELCOME
    welcome_title: str = "WELCOME"
    welcome_accent: str = "C9DDF0"
    welcome_dim: int = 35
    welcome_custom_background: bool = False
    member_log_channel_id: int | None = None


@dataclass(frozen=True, slots=True)
class WelcomeArtwork:
    background: bytes | None = None
    title: str = "WELCOME"
    accent: str = "C9DDF0"
    dim: int = 35


class SettingsRepository:
    def __init__(self, connection: aiosqlite.Connection) -> None:
        self.connection = connection

    async def save_member_inviter(self, guild_id, member_id, inviter_id):
        await self.connection.execute(
            "INSERT INTO member_inviters (guild_id, member_id, inviter_id) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id, member_id) DO UPDATE SET inviter_id = excluded.inviter_id",
            (str(guild_id), str(member_id), str(inviter_id) if inviter_id is not None else None),
        )

    async def get_member_inviter(self, guild_id, member_id):
        async with self.connection.execute(
            "SELECT inviter_id FROM member_inviters WHERE guild_id = ? AND member_id = ?",
            (str(guild_id), str(member_id)),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    async def get_designs(self, guild_id):
        async with self.connection.execute(
            "SELECT revision, profiles FROM embed_designs WHERE guild_id = ?", (str(guild_id),)
        ) as cursor:
            row = await cursor.fetchone()
        return (row[0], json.loads(row[1])) if row else (None, {})

    async def apply_designs(self, guild_id, profiles, expected):
        """One compare-and-swap updates every selected menu together, across processes."""
        revision = uuid.uuid4().hex
        values = (str(guild_id), revision, json.dumps(profiles))
        if expected is None:
            sql = (
                "INSERT INTO embed_designs(guild_id,revision,profiles) VALUES (?,?,?) "
                "ON CONFLICT(guild_id) DO NOTHING RETURNING revision"
            )
        else:
            sql = (
                "UPDATE embed_designs SET revision=?,profiles=? "
                "WHERE guild_id=? AND revision=? RETURNING revision"
            )
            values = (revision, json.dumps(profiles), str(guild_id), expected)
        async with self.connection.execute(sql, values) as cursor:
            if not await cursor.fetchone():
                raise ValueError("Another administrator changed the designs. Reopen Embed Design.")
        return revision

    async def get(self, guild_id: int) -> GuildSettings:
        async with self.connection.execute(
            "SELECT channel_id FROM member_log_settings WHERE guild_id = ?", (str(guild_id),)
        ) as cursor:
            member_log = await cursor.fetchone()
        async with self.connection.execute(
            "SELECT log_channel_id FROM guild_settings WHERE guild_id = ?", (str(guild_id),)
        ) as cursor:
            row = await cursor.fetchone()
        async with self.connection.execute(
            "SELECT channel_id, message FROM welcome_settings WHERE guild_id = ?", (str(guild_id),)
        ) as cursor:
            welcome = await cursor.fetchone()
        async with self.connection.execute(
            "SELECT title, accent, dim, background IS NOT NULL "
            "FROM welcome_artwork WHERE guild_id = ?",
            (str(guild_id),),
        ) as cursor:
            art = await cursor.fetchone()
        return GuildSettings(
            guild_id,
            int(row[0]) if row and row[0] else None,
            int(welcome[0]) if welcome and welcome[0] else None,
            welcome[1] if welcome else DEFAULT_WELCOME,
            art[0] if art else "WELCOME",
            art[1] if art else "C9DDF0",
            art[2] if art else 35,
            bool(art[3]) if art else False,
            int(member_log[0]) if member_log else None,
        )

    async def get_welcome_artwork(self, guild_id: int) -> WelcomeArtwork:
        async with self.connection.execute(
            "SELECT background, title, accent, dim FROM welcome_artwork WHERE guild_id = ?",
            (str(guild_id),),
        ) as cursor:
            row = await cursor.fetchone()
        return WelcomeArtwork(*row) if row else WelcomeArtwork()

    async def set_welcome_background(self, guild_id: int, background: bytes | None) -> None:
        await self.connection.execute(
            "INSERT INTO welcome_artwork (guild_id, background) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET background = excluded.background",
            (str(guild_id), background),
        )

    async def set_welcome_appearance(
        self, guild_id: int, title: str, accent: str, dim: int
    ) -> None:
        await self.connection.execute(
            "INSERT INTO welcome_artwork (guild_id, title, accent, dim) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET title = excluded.title, "
            "accent = excluded.accent, dim = excluded.dim",
            (str(guild_id), title, accent, dim),
        )

    async def set_welcome_channel(self, guild_id: int, channel_id: int | None) -> None:
        await self.connection.execute(
            "INSERT INTO welcome_settings (guild_id, channel_id, message) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id",
            (str(guild_id), str(channel_id) if channel_id is not None else None, DEFAULT_WELCOME),
        )

    async def set_welcome_message(self, guild_id: int, message: str) -> None:
        await self.connection.execute(
            "INSERT INTO welcome_settings (guild_id, message) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET message = excluded.message",
            (str(guild_id), message),
        )

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

    async def set_member_log_channel(self, guild_id: int, channel_id: int) -> None:
        await self.connection.execute(
            "INSERT INTO member_log_settings (guild_id, channel_id) VALUES (?, ?) "
            "ON CONFLICT(guild_id) DO UPDATE SET channel_id = excluded.channel_id",
            (str(guild_id), str(channel_id)),
        )

    async def clear_member_log_channel(self, guild_id: int) -> None:
        await self.connection.execute(
            "DELETE FROM member_log_settings WHERE guild_id = ?", (str(guild_id),)
        )


@asynccontextmanager
async def open_settings(path: Path, *, database_url: str | None = None):
    if database_url:
        from ky_bot.storage.postgres import open_postgres_settings

        async with open_postgres_settings(database_url) as repository:
            yield repository
        return
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(str(path), isolation_level=None, timeout=10) as connection:
        async with connection.execute("PRAGMA user_version") as cursor:
            version = (await cursor.fetchone())[0]
        if version > 3:
            raise RuntimeError("Database schema is newer than this version of KY BOT.")
        if version == 0:
            await connection.executescript(
                "BEGIN IMMEDIATE;"
                "CREATE TABLE IF NOT EXISTS guild_settings ("
                "guild_id TEXT PRIMARY KEY, log_channel_id TEXT NOT NULL);"
                "PRAGMA user_version = 1;"
                "COMMIT;"
            )
        if version < 2:
            await connection.executescript(
                "BEGIN IMMEDIATE;"
                "CREATE TABLE welcome_settings (guild_id TEXT PRIMARY KEY, "
                "channel_id TEXT, message TEXT NOT NULL);"
                "PRAGMA user_version = 2;"
                "COMMIT;"
            )
        if version < 3:
            await connection.executescript(
                "BEGIN IMMEDIATE;"
                "CREATE TABLE welcome_artwork (guild_id TEXT PRIMARY KEY, background BLOB, "
                "title TEXT NOT NULL DEFAULT 'WELCOME', accent TEXT NOT NULL DEFAULT 'C9DDF0', "
                "dim INTEGER NOT NULL DEFAULT 35);"
                "PRAGMA user_version = 3;COMMIT;"
            )
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS embed_designs "
            "(guild_id TEXT PRIMARY KEY, revision TEXT NOT NULL, profiles TEXT NOT NULL)"
        )
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS member_log_settings "
            "(guild_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL)"
        )
        await connection.execute(
            "CREATE TABLE IF NOT EXISTS member_inviters "
            "(guild_id TEXT NOT NULL, member_id TEXT NOT NULL, inviter_id TEXT, "
            "PRIMARY KEY(guild_id, member_id))"
        )
        yield SettingsRepository(connection)
