"""Shared SQLite/PostgreSQL XP with atomic cooldowns and revisioned configuration."""

import asyncio
import json
import secrets
import uuid
from contextlib import asynccontextmanager
from math import isqrt

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS leveling_settings (guild BIGINT PRIMARY KEY, "
    "revision TEXT NOT NULL, settings TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS leveling_members (guild BIGINT NOT NULL, member BIGINT NOT NULL, "
    "xp BIGINT NOT NULL DEFAULT 0, message_at DOUBLE PRECISION NOT NULL DEFAULT 0, "
    "voice_at DOUBLE PRECISION NOT NULL DEFAULT 0, source BIGINT NOT NULL DEFAULT 0, "
    "fingerprint TEXT NOT NULL DEFAULT '', PRIMARY KEY(guild,member))",
    "CREATE INDEX IF NOT EXISTS leveling_rank_idx ON leveling_members(guild,xp DESC,member)",
)
MAX_LEVEL = 10000
MAX_XP = 50 * MAX_LEVEL * (MAX_LEVEL + 1)


def level_for(xp):
    return min(MAX_LEVEL, (isqrt(1 + 4 * (max(0, xp) // 50)) - 1) // 2)


def threshold(level):
    return 50 * level * (level + 1)


def defaults():
    return dict(
        enabled=True,
        message_enabled=True,
        message_min=15,
        message_max=25,
        message_cooldown=60,
        voice_enabled=False,
        voice_xp=5,
        voice_interval=60,
        excluded_channels=[],
        excluded_roles=[],
        announcement_channel=None,
        rewards={},
    )


def validate(settings):
    result = defaults() | settings
    if set(result) != set(defaults()):
        raise ValueError("Unknown leveling setting.")
    for key in ("enabled", "message_enabled", "voice_enabled"):
        if type(result[key]) is not bool:
            raise ValueError("Choose on or off.")
    for key, low, high in (
        ("message_min", 1, 1000),
        ("message_max", 1, 1000),
        ("message_cooldown", 5, 86400),
        ("voice_xp", 1, 1000),
        ("voice_interval", 30, 86400),
    ):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"{key.replace('_', ' ').capitalize()}: use {low}–{high}.")
    if result["message_min"] > result["message_max"]:
        raise ValueError("Minimum message XP cannot exceed maximum XP.")
    for key in ("excluded_channels", "excluded_roles"):
        values = result[key]
        if not isinstance(values, list) or len(values) > 25:
            raise ValueError("Choose up to 25 exclusions of each type.")
        if any(type(v) is not int or not 0 < v < 2**63 for v in values):
            raise ValueError("Choose valid channels or roles in this server.")
        result[key] = sorted(set(values))
    channel = result["announcement_channel"]
    if channel is not None and (type(channel) is not int or not 0 < channel < 2**63):
        raise ValueError("Choose a valid announcement channel.")
    rewards = result["rewards"]
    if not isinstance(rewards, dict) or len(rewards) > 25:
        raise ValueError("Use up to 25 role rewards.")
    for key, role in rewards.items():
        if not str(key).isdigit() or str(int(key)) != key or not 1 <= int(key) <= MAX_LEVEL:
            raise ValueError(f"Reward levels must be between 1 and {MAX_LEVEL}.")
        if type(role) is not int or not 0 < role < 2**63:
            raise ValueError("Choose a valid reward role.")
    return result


class LevelStore:
    def __init__(self, connection, *, postgres=False):
        self.db, self.postgres = connection, postgres
        self.lock = asyncio.Lock()

    @classmethod
    async def open(cls, path, database_url=None):
        if database_url:
            import psycopg

            db = await psycopg.AsyncConnection.connect(
                database_url,
                autocommit=True,
                connect_timeout=10,
                options="-c statement_timeout=5000 -c lock_timeout=5000",
            )
        else:
            from pathlib import Path

            import aiosqlite

            Path(path).parent.mkdir(parents=True, exist_ok=True)
            db = await aiosqlite.connect(path, isolation_level=None, timeout=10)
        store = cls(db, postgres=bool(database_url))
        try:
            async with store.transaction(0):
                for statement in SCHEMA:
                    await store.execute(statement)
        except BaseException:
            await db.close()
            raise
        return store

    async def execute(self, sql, values=(), *, all_rows=False):
        cursor = await self.db.execute(sql.replace("?", "%s") if self.postgres else sql, values)
        try:
            if sql.lstrip().upper().startswith("SELECT"):
                return await cursor.fetchall() if all_rows else await cursor.fetchone()
        finally:
            await cursor.close()

    @asynccontextmanager
    async def transaction(self, guild):
        async with self.lock:
            await self.execute("BEGIN" if self.postgres else "BEGIN IMMEDIATE")
            try:
                if self.postgres:
                    # Shared by all bot/web processes using this repository.
                    await self.execute("SELECT pg_advisory_xact_lock(?)", (guild,))
                yield
                await self.execute("COMMIT")
            except BaseException:
                await asyncio.shield(self.execute("ROLLBACK"))
                raise

    async def _config(self, guild):
        row = await self.execute(
            "SELECT revision,settings FROM leveling_settings WHERE guild=?", (guild,)
        )
        return (row[0], defaults() | json.loads(row[1])) if row else (None, defaults())

    async def config(self, guild):
        async with self.lock:
            return await self._config(guild)

    async def configure(self, guild, expected, **changes):
        async with self.transaction(guild):
            revision, config = await self._config(guild)
            if revision != expected:
                raise ValueError("Another administrator changed leveling. Refresh and try again.")
            config = validate(config | changes)
            revision = uuid.uuid4().hex
            await self.execute(
                "INSERT INTO leveling_settings(guild,revision,settings) VALUES (?,?,?) "
                "ON CONFLICT(guild) DO UPDATE SET revision=excluded.revision, "
                "settings=excluded.settings",
                (guild, revision, json.dumps(config)),
            )
            return revision, config

    async def award(
        self,
        guild,
        member,
        kind,
        now,
        *,
        source=0,
        fingerprint="",
        channels=(),
        roles=(),
        expected=None,
    ):
        """Recheck rules under the same lock as XP/cooldowns; replayed events cannot award twice."""
        if kind not in ("message", "voice"):
            raise ValueError("Unknown XP activity.")
        async with self.transaction(guild):
            revision, config = await self._config(guild)
            if (
                not config["enabled"]
                or not config[kind + "_enabled"]
                or set(channels) & set(config["excluded_channels"])
                or set(roles) & set(config["excluded_roles"])
                or (kind == "voice" and revision != expected)
            ):
                return None
            await self.execute(
                "INSERT INTO leveling_members(guild,member) VALUES (?,?) "
                "ON CONFLICT(guild,member) DO NOTHING",
                (guild, member),
            )
            row = await self.execute(
                "SELECT xp,message_at,voice_at,source,fingerprint FROM leveling_members "
                "WHERE guild=? AND member=?",
                (guild, member),
            )
            xp, message_at, voice_at, previous_source, previous_text = row
            if kind == "message":
                if (
                    source <= previous_source
                    or now - message_at < config["message_cooldown"]
                    or (fingerprint == previous_text and now - message_at < 300)
                ):
                    return None
                amount = config["message_min"] + secrets.randbelow(
                    config["message_max"] - config["message_min"] + 1
                )
                message_at, previous_source, previous_text = now, source, fingerprint
            else:
                if now - voice_at < config["voice_interval"]:
                    return None
                amount, voice_at = config["voice_xp"], now
            new_xp = min(MAX_XP, xp + amount)
            await self.execute(
                "UPDATE leveling_members SET xp=?,message_at=?,voice_at=?,source=?,fingerprint=? "
                "WHERE guild=? AND member=?",
                (new_xp, message_at, voice_at, previous_source, previous_text, guild, member),
            )
            return dict(xp=new_xp, before=level_for(xp), level=level_for(new_xp), config=config)

    async def leaderboard(self, guild, page=0):
        async with self.lock:
            total = await self.execute(
                "SELECT COUNT(*) FROM leveling_members WHERE guild=? AND xp>0", (guild,)
            )
            page = min(max(0, page), max(0, (total[0] - 1) // 10))
            rows = await self.execute(
                "SELECT member,xp FROM leveling_members WHERE guild=? AND xp>0 "
                "ORDER BY xp DESC,member LIMIT 10 OFFSET ?",
                (guild, page * 10),
                all_rows=True,
            )
            return page, total[0], rows

    async def close(self):
        await self.db.close()
