"""Transactional requests, confirmation drafts and retryable delivery events."""

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS ask_settings (guild BIGINT PRIMARY KEY, revision TEXT NOT NULL, "
    "settings TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ask_requests (id TEXT PRIMARY KEY, guild BIGINT NOT NULL, "
    "sender BIGINT NOT NULL, recipient BIGINT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL, "
    "created DOUBLE PRECISION NOT NULL, data TEXT NOT NULL)",
    "CREATE UNIQUE INDEX IF NOT EXISTS ask_active_pair "
    "ON ask_requests(guild,sender,recipient,kind) "
    "WHERE state IN ('pending','accepted')",
    "CREATE TABLE IF NOT EXISTS ask_drafts (id TEXT PRIMARY KEY, guild BIGINT NOT NULL, "
    "sender BIGINT NOT NULL, created DOUBLE PRECISION NOT NULL, request TEXT, data TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS ask_events (id TEXT PRIMARY KEY, request TEXT NOT NULL, "
    "state TEXT NOT NULL, created DOUBLE PRECISION NOT NULL, done BIGINT NOT NULL DEFAULT 0, "
    "data TEXT NOT NULL)",
    "CREATE INDEX IF NOT EXISTS ask_event_queue ON ask_events(done,created)",
)
KINDS = {"dm": "Direct message", "friend": "Friend request", "roleplay": "Roleplay consent"}


def defaults():
    return dict(
        enabled=False,
        channel=0,
        log_channel=0,
        panel=0,
        panel_channel=0,
        public=False,
        sender_roles=[],
        recipient_roles=[],
        blocked_sender_roles=[],
        blocked_recipient_roles=[],
    )


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
            async with store.transaction(0):
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
    async def transaction(self, guild):
        async with self.lock:
            await self.execute("BEGIN" if self.postgres else "BEGIN IMMEDIATE")
            try:
                if self.postgres:
                    await self.execute("SELECT pg_advisory_xact_lock(?)", (1264141141,))
                yield
                await self.execute("COMMIT")
            except BaseException:
                await asyncio.shield(self.execute("ROLLBACK"))
                raise

    async def _config(self, guild):
        row = await self.execute(
            "SELECT revision,settings FROM ask_settings WHERE guild=?", (guild,)
        )
        return (row[0], defaults() | json.loads(row[1])) if row else (None, defaults())

    async def config(self, guild):
        async with self.lock:
            return await self._config(guild)

    async def configure(self, guild, expected, **changes):
        if set(changes) - set(defaults()):
            raise ValueError("Unknown request setting.")
        async with self.transaction(guild):
            revision, config = await self._config(guild)
            if revision != expected:
                raise ValueError("Settings changed. Refresh this menu and try again.")
            config.update(changes)
            if config["enabled"] and not (config["channel"] and config["log_channel"]):
                raise ValueError("Choose request and staff log channels before enabling.")
            if config["channel"] and config["channel"] == config["log_channel"]:
                raise ValueError("Use a separate staff-only log channel.")
            revision = uuid.uuid4().hex
            await self.execute(
                "INSERT INTO ask_settings(guild,revision,settings) VALUES (?,?,?) "
                "ON CONFLICT(guild) DO UPDATE SET revision=excluded.revision,"
                "settings=excluded.settings",
                (guild, revision, json.dumps(config)),
            )
            return revision, config

    async def draft(self, guild, sender, recipient, kind, reason):
        if kind not in KINDS or len(reason) > 500:
            raise ValueError("Choose a request type and keep the reason under 500 characters.")
        token = uuid.uuid4().hex
        async with self.transaction(guild):
            await self.execute(
                "INSERT INTO ask_drafts VALUES (?,?,?,?,?,?)",
                (
                    token,
                    guild,
                    sender,
                    time.time(),
                    None,
                    json.dumps(dict(recipient=recipient, kind=kind, reason=reason)),
                ),
            )
        return token

    async def get_draft(self, token):
        async with self.lock:
            row = await self.execute(
                "SELECT guild,sender,created,request,data FROM ask_drafts WHERE id=?", (token,)
            )
        return (
            dict(guild=row[0], sender=row[1], created=row[2], request=row[3], **json.loads(row[4]))
            if row
            else None
        )

    async def cancel_draft(self, token, actor):
        async with self.lock:
            await self.execute(
                "DELETE FROM ask_drafts WHERE id=? AND sender=? AND request IS NULL", (token, actor)
            )

    async def reserve(self, token, actor, expected, names):
        draft = await self.get_draft(token)
        if not draft or draft["sender"] != actor:
            raise ValueError("This confirmation belongs to another member or has expired.")
        async with self.transaction(draft["guild"]):
            row = await self.execute("SELECT request FROM ask_drafts WHERE id=?", (token,))
            if not row:
                raise ValueError("This confirmation has expired.")
            if row[0]:
                return row[0]
            revision, config = await self._config(draft["guild"])
            if revision != expected or not config["enabled"]:
                raise ValueError("Request settings changed. Open a new request.")
            if time.time() - draft["created"] > 900:
                raise ValueError("This confirmation has expired. Open a new request.")
            pair = (draft["guild"], actor, draft["recipient"], draft["kind"])
            if await self.execute(
                "SELECT id FROM ask_requests WHERE guild=? AND sender=? "
                "AND recipient=? AND kind=? AND state IN ('pending','accepted')",
                pair,
            ):
                raise ValueError("You already have a pending or accepted request of this type.")
            # A modest server-scoped limit prevents repeated requests to many members.
            count = await self.execute(
                "SELECT COUNT(*) FROM ask_requests WHERE guild=? AND sender=? AND created>?",
                (pair[0], actor, time.time() - 3600),
            )
            if count[0] >= 10:
                raise ValueError("Please wait before sending more requests (10 per hour).")
            rid = uuid.uuid4().hex
            data = dict(
                reason=draft["reason"],
                names=names,
                channel=config["channel"],
                log_channel=config["log_channel"],
                public=config["public"],
            )
            await self.execute(
                "INSERT INTO ask_requests VALUES (?,?,?,?,?,?,?,?)",
                (rid, *pair, "pending", time.time(), json.dumps(data)),
            )
            await self.execute("UPDATE ask_drafts SET request=? WHERE id=?", (rid, token))
            await self._event(rid, "pending")
            return rid

    async def _event(self, rid, state):
        await self.execute(
            "INSERT INTO ask_events VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, rid, state, time.time(), 0, "{}"),
        )

    async def get(self, rid):
        async with self.lock:
            row = await self.execute("SELECT * FROM ask_requests WHERE id=?", (rid,))
        return (
            dict(
                zip(("id", "guild", "sender", "recipient", "kind", "state", "created", "data"), row)
            )
            if row
            else None
        )

    async def transition(self, rid, actor, state, reason=""):
        request = await self.get(rid)
        if not request:
            raise ValueError("This request is unavailable.")
        async with self.transaction(request["guild"]):
            row = await self.execute(
                "SELECT state,created,data FROM ask_requests WHERE id=?", (rid,)
            )
            expected = "accepted" if state == "revoked" else "pending"
            owner = request["sender"] if state == "cancelled" else request["recipient"]
            if actor != owner:
                raise ValueError("Only the appropriate request participant can do that.")
            if state not in {"accepted", "denied", "revoked", "cancelled"}:
                raise ValueError("Unknown request action.")
            if row[0] != expected or (expected == "pending" and row[1] + 172800 <= time.time()):
                raise ValueError("This request has already been handled or has expired.")
            data = json.loads(row[2])
            data.update(decline_reason=reason[:500], resolved=time.time())
            await self.execute(
                "UPDATE ask_requests SET state=?,data=? WHERE id=?", (state, json.dumps(data), rid)
            )
            await self._event(rid, state)

    async def expire(self):
        async with self.transaction(0):
            rows = await self.execute(
                "SELECT id,data FROM ask_requests WHERE state='pending' AND created<=?",
                (time.time() - 172800,),
                many=True,
            )
            for rid, raw in rows:
                data = json.loads(raw) | dict(resolved=time.time())
                await self.execute(
                    "UPDATE ask_requests SET state='expired',data=? WHERE id=?",
                    (json.dumps(data), rid),
                )
                await self._event(rid, "expired")
            await self.execute("DELETE FROM ask_drafts WHERE created<?", (time.time() - 86400,))

    async def inbox(self, guild, actor):
        async with self.lock:
            return await self.execute(
                "SELECT id,kind,state,sender,recipient FROM ask_requests "
                "WHERE guild=? AND (sender=? OR recipient=?) "
                "AND state IN ('pending','accepted') ORDER BY created DESC "
                "LIMIT 25",
                (guild, actor, actor),
                many=True,
            )

    async def events(self):
        async with self.lock:
            return await self.execute(
                "SELECT id,request,state,data FROM ask_events WHERE done=0 "
                "ORDER BY created,id LIMIT 100",
                many=True,
            )

    async def checkpoint(self, eid, data, done=False):
        async with self.lock:
            await self.execute(
                "UPDATE ask_events SET data=?,done=? WHERE id=?", (json.dumps(data), int(done), eid)
            )

    async def log_reference(self, rid):
        async with self.lock:
            rows = await self.execute(
                "SELECT data FROM ask_events WHERE request=? ORDER BY created,id",
                (rid,),
                many=True,
            )
        for (raw,) in rows:
            receipt = json.loads(raw)
            if receipt.get("log"):
                return receipt["log"], receipt.get("log_channel")
        return None

    async def save_log_reference(self, rid, message, channel):
        # Keep all event receipts on the same canonical message, including legacy receipts.
        async with self.transaction(0):
            rows = await self.execute(
                "SELECT id,data FROM ask_events WHERE request=?",
                (rid,),
                many=True,
            )
            for eid, raw in rows:
                receipt = json.loads(raw) | dict(log=message, log_channel=channel)
                await self.execute(
                    "UPDATE ask_events SET data=? WHERE id=?",
                    (json.dumps(receipt), eid),
                )

    async def pending_count(self):
        async with self.lock:
            return (await self.execute("SELECT COUNT(*) FROM ask_events WHERE done=0"))[0]

    async def close(self):
        await self.db.close()
