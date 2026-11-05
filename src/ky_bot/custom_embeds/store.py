"""SQLite/PostgreSQL drafts, published snapshots and sticky counters."""

import asyncio
import json
import uuid
from contextlib import asynccontextmanager

from .model import defaults, validate

COLUMNS = (
    "id",
    "guild",
    "revision",
    "draft",
    "published",
    "channel",
    "message",
    "message_count",
    "last_seen",
    "sticky_every",
    "pending",
    "retired",
    "deleted",
)
SCHEMA = (
    "CREATE TABLE IF NOT EXISTS custom_embeds (id TEXT PRIMARY KEY, guild BIGINT NOT NULL, "
    "revision TEXT NOT NULL, draft TEXT NOT NULL, published TEXT NOT NULL DEFAULT '', "
    "channel BIGINT NOT NULL DEFAULT 0, message BIGINT NOT NULL DEFAULT 0, "
    "message_count BIGINT NOT NULL DEFAULT 0, last_seen BIGINT NOT NULL DEFAULT 0, "
    "sticky_every BIGINT NOT NULL DEFAULT 0, pending BIGINT NOT NULL DEFAULT 0, "
    "retired TEXT NOT NULL DEFAULT '[]', deleted BIGINT NOT NULL DEFAULT 0)",
    "CREATE INDEX IF NOT EXISTS custom_embed_channel ON custom_embeds(guild,channel,sticky_every)",
)


def record(row):
    if row is None:
        return None
    result = dict(zip(COLUMNS, row))
    for key in ("draft", "retired"):
        result[key] = json.loads(result[key])
    result["published"] = json.loads(result["published"]) if result["published"] else None
    return result


class Store:
    def __init__(self, db, postgres=False):
        self.db, self.postgres = db, postgres
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

            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            db = await aiosqlite.connect(str(path), isolation_level=None, timeout=10)
        store = cls(db, bool(database_url))
        try:
            async with store.transaction():
                for sql in SCHEMA:
                    await store.execute(sql)
        except BaseException:
            await db.close()
            raise
        return store

    async def execute(self, sql, args=(), *, many=False):
        cursor = await self.db.execute(sql.replace("?", "%s") if self.postgres else sql, args)
        try:
            if sql.lstrip().upper().startswith("SELECT"):
                return await cursor.fetchall() if many else await cursor.fetchone()
        finally:
            await cursor.close()

    @asynccontextmanager
    async def transaction(self):
        async with self.lock:
            await self.execute("BEGIN" if self.postgres else "BEGIN IMMEDIATE")
            try:
                if self.postgres:
                    await self.execute("SELECT pg_advisory_xact_lock(?)", (1264141143,))
                yield
                await self.execute("COMMIT")
            except BaseException:
                await asyncio.shield(self.execute("ROLLBACK"))
                raise

    async def _get(self, guild, ident):
        result = record(
            await self.execute("SELECT * FROM custom_embeds WHERE guild=? AND id=?", (guild, ident))
        )
        if not result:
            raise ValueError("This saved embed is no longer available in this server.")
        return result

    async def get(self, guild, ident):
        async with self.lock:
            return await self._get(guild, ident)

    async def create(self, guild):
        ident, revision = uuid.uuid4().hex, uuid.uuid4().hex
        async with self.lock:
            await self.execute(
                "INSERT INTO custom_embeds(id,guild,revision,draft) VALUES (?,?,?,?)",
                (ident, guild, revision, json.dumps(defaults())),
            )
        return ident

    async def listing(self, guild):
        async with self.lock:
            rows = await self.execute(
                "SELECT id,draft,channel,message,deleted FROM custom_embeds "
                "WHERE guild=? ORDER BY id",
                (guild,),
                many=True,
            )
        return [(r[0], json.loads(r[1])["name"], r[2], r[3], r[4]) for r in rows]

    async def save(self, guild, ident, expected, **changes):
        if set(changes) - set(defaults()):
            raise ValueError("Unknown embed setting.")
        async with self.transaction():
            current = await self._get(guild, ident)
            if current["revision"] != expected or current["deleted"]:
                raise ValueError("This embed changed. Refresh the editor before saving.")
            draft = validate(current["draft"] | changes)
            revision = uuid.uuid4().hex
            await self.execute(
                "UPDATE custom_embeds SET draft=?,revision=? WHERE id=? AND guild=?",
                (json.dumps(draft), revision, ident, guild),
            )
        return revision

    async def published(
        self, guild, ident, expected, draft, channel, message, *, moved=False, consumed=0
    ):
        async with self.transaction():
            current = await self._get(guild, ident)
            if current["deleted"] or current["message"] != expected:
                raise ValueError("The published embed changed. Refresh and try again.")
            retired = current["retired"]
            old = dict(channel=current["channel"], message=current["message"])
            if (
                old["message"]
                and old != dict(channel=channel, message=message)
                and old not in retired
            ):
                retired.append(old)
            count = max(0, current["message_count"] - consumed) if moved else 0
            seen = max(current["last_seen"], message)
            every = draft["sticky_every"]
            pending = int(bool(every and count >= every))
            await self.execute(
                "UPDATE custom_embeds SET published=?,channel=?,message=?,"
                "message_count=?,last_seen=?,sticky_every=?,pending=?,retired=? "
                "WHERE id=? AND guild=?",
                (
                    json.dumps(draft),
                    channel,
                    message,
                    count,
                    seen,
                    every,
                    pending,
                    json.dumps(retired),
                    ident,
                    guild,
                ),
            )

    async def note(self, guild, channel, message):
        async with self.lock:
            await self.execute(
                "UPDATE custom_embeds SET message_count=message_count+1, "
                "pending=CASE WHEN message_count+1>=sticky_every THEN 1 ELSE pending END, "
                "last_seen=? "
                "WHERE guild=? AND channel=? AND message<>0 AND sticky_every>0 "
                "AND deleted=0 AND last_seen<?",
                (message, guild, channel, message),
            )

    async def targets(self, guild, channel):
        async with self.lock:
            return await self.execute(
                "SELECT id FROM custom_embeds WHERE guild=? AND channel=? "
                "AND message<>0 AND sticky_every>0 AND deleted=0",
                (guild, channel),
                many=True,
            )

    async def note_one(self, guild, ident, message):
        async with self.lock:
            await self.execute(
                "UPDATE custom_embeds SET message_count=message_count+1, "
                "pending=CASE WHEN message_count+1>=sticky_every THEN 1 ELSE pending END, "
                "last_seen=? "
                "WHERE guild=? AND id=? AND message<>0 AND sticky_every>0 "
                "AND deleted=0 AND last_seen<?",
                (message, guild, ident, message),
            )

    async def due(self):
        async with self.lock:
            return await self.execute(
                "SELECT guild,id FROM custom_embeds WHERE pending=1 OR deleted=1 OR retired<>'[]'",
                many=True,
            )

    async def sticky_index(self):
        async with self.lock:
            return await self.execute(
                "SELECT guild,id FROM custom_embeds WHERE "
                "sticky_every>0 AND message<>0 AND deleted=0",
                many=True,
            )

    async def forget(self, guild, ident, obsolete):
        async with self.transaction():
            current = await self._get(guild, ident)
            retired = [r for r in current["retired"] if r != obsolete]
            await self.execute(
                "UPDATE custom_embeds SET retired=? WHERE id=? AND guild=?",
                (json.dumps(retired), ident, guild),
            )

    async def delete(self, guild, ident, expected):
        async with self.transaction():
            current = await self._get(guild, ident)
            if current["revision"] != expected:
                raise ValueError("The embed changed. Refresh before deleting it.")
            await self.execute(
                "UPDATE custom_embeds SET deleted=1,sticky_every=0,pending=0 "
                "WHERE guild=? AND id=?",
                (guild, ident),
            )

    async def erase(self, guild, ident):
        async with self.lock:
            await self.execute(
                "DELETE FROM custom_embeds WHERE guild=? AND id=? AND deleted=1", (guild, ident)
            )

    async def acknowledge(self, guild, ident):
        async with self.lock:
            await self.execute(
                "UPDATE custom_embeds SET message_count=0,pending=0 WHERE guild=? AND id=?",
                (guild, ident),
            )

    async def close(self):
        await self.db.close()
