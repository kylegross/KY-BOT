"""Persistent, revision-checked configuration and atomic confession state transitions."""

import asyncio
import hashlib
import hmac
import json
import secrets
import time
import uuid
from contextlib import asynccontextmanager

SCHEMA = (
    (
        "CREATE TABLE IF NOT EXISTS confession_settings (guild BIGINT NOT "
        "NULL, channel BIGINT NOT NULL, revision TEXT NOT NULL, config TEXT "
        "NOT NULL, PRIMARY KEY(guild,channel))"
    ),
    (
        "CREATE TABLE IF NOT EXISTS confession_secrets (guild BIGINT PRIMARY "
        "KEY, secret TEXT NOT NULL)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS confession_posts (id TEXT PRIMARY KEY, "
        "operation BIGINT UNIQUE NOT NULL, guild BIGINT NOT NULL, channel "
        "BIGINT NOT NULL, author BIGINT, author_key TEXT NOT NULL, body TEXT "
        "NOT NULL, parent TEXT, state TEXT NOT NULL, created DOUBLE PRECISION "
        "NOT NULL, data TEXT NOT NULL)"
    ),
    (
        "CREATE INDEX IF NOT EXISTS confession_author_idx ON "
        "confession_posts(guild,author_key,created)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS confession_bans (guild BIGINT NOT NULL, "
        "author_key TEXT NOT NULL, reason TEXT NOT NULL, moderator BIGINT NOT "
        "NULL, PRIMARY KEY(guild,author_key))"
    ),
    (
        "CREATE TABLE IF NOT EXISTS confession_reports (id TEXT PRIMARY KEY, "
        "guild BIGINT NOT NULL, post TEXT NOT NULL, reporter BIGINT NOT NULL, "
        "reason TEXT NOT NULL, state TEXT NOT NULL, UNIQUE(post,reporter))"
    ),
    (
        "CREATE TABLE IF NOT EXISTS confession_panels (guild BIGINT NOT NULL, "
        "channel BIGINT NOT NULL, message BIGINT NOT NULL DEFAULT 0, count "
        "BIGINT NOT NULL DEFAULT 0, retired TEXT NOT NULL DEFAULT '[]', "
        "PRIMARY KEY(guild,channel))"
    ),
)


def defaults():
    return dict(
        enabled=False,
        color="D6E2F3",
        header_title="CONFESSIONS",
        artwork_finish="silver_neon",
        text_finish="silver_neon",
        text_color=None,
        logging=False,
        log_channel=0,
        approval=False,
        queue_channel=0,
        replies=True,
        reports=False,
        bans=True,
        sticky=False,
        sticky_count=5,
        cooldown=60,
        max_length=2000,
        title="ANONYMOUS CONFESSION",
        panel_title="GOT A SECRET, A HOT TAKE, OR A THOUGHT YOU CAN'T SAY OUTLOUD?",
        panel_text=(
            "Drop it here. Your words get the spotlight—your name stays off the public post."
        ),
        submit_label="Spill a secret",
        footer_text="KY BOT · Confessions",
        header=None,
        footer=None,
        allowed_roles=[],
        blocked_roles=[],
    )


def validate(config):
    result = defaults() | config
    if set(result) != set(defaults()):
        raise ValueError("Unknown confession setting.")
    for key in ("enabled", "logging", "approval", "replies", "reports", "bans", "sticky"):
        if type(result[key]) is not bool:
            raise ValueError("Choose on or off.")
    for key, low, high in (
        ("sticky_count", 1, 100),
        ("cooldown", 0, 86400),
        ("max_length", 1, 2000),
    ):
        if type(result[key]) is not int or not low <= result[key] <= high:
            raise ValueError(f"{key.replace('_', ' ')} must be {low}–{high}.")
    for key in ("log_channel", "queue_channel"):
        if type(result[key]) is not int or not 0 <= result[key] < 2**63:
            raise ValueError("Choose a valid channel.")
    if (result["logging"] and not result["log_channel"]) or (
        (result["approval"] or result["reports"]) and not result["queue_channel"]
    ):
        raise ValueError("Choose the private log/review destination before enabling this option.")
    if len(result["color"]) != 6 or any(c not in "0123456789abcdefABCDEF" for c in result["color"]):
        raise ValueError("Use a six-digit hex color, for example D6E2F3.")
    from ky_bot.design.menu_design import FINISHES
    from ky_bot.design.text_colour import text_colour

    for key in ("artwork_finish", "text_finish"):
        if result[key] not in FINISHES:
            raise ValueError("Choose one of the four standard finishes.")
    result["text_color"] = text_colour(result["text_color"])
    for key, limit in (
        ("title", 80),
        ("header_title", 80),
        ("panel_title", 80),
        ("panel_text", 1000),
        ("submit_label", 80),
        ("footer_text", 200),
    ):
        if not isinstance(result[key], str) or len(result[key]) > limit:
            raise ValueError(f"{key.replace('_', ' ')} must be at most {limit} characters.")
    if any(not result[key].strip() for key in ("title", "panel_title", "header_title")):
        raise ValueError("Titles cannot be empty.")
    for key in ("allowed_roles", "blocked_roles"):
        if (
            not isinstance(result[key], list)
            or len(result[key]) > 25
            or any(type(v) is not int or not 0 < v < 2**63 for v in result[key])
        ):
            raise ValueError("Choose up to 25 valid roles.")
    for key in ("header", "footer"):
        if result[key] is not None and (
            not isinstance(result[key], str) or len(result[key]) > 2_000_000
        ):
            raise ValueError("Artwork must be an uploaded image within the size limit.")
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
        result = cls(db, bool(database_url))
        try:
            async with result.transaction():
                for sql in SCHEMA:
                    await result.execute(sql)
                await result.backfill_numbers()
        except BaseException:
            await db.close()
            raise
        return result

    async def backfill_numbers(self):
        """Give existing posts stable display numbers without changing action tokens."""
        rows = await self.execute(
            "SELECT id,guild,channel,parent,data FROM confession_posts ORDER BY created,operation",
            many=True,
        )
        counters, numbers, changed = {}, {}, []
        for token, guild, channel, parent, raw in rows:
            data = json.loads(raw)
            key = guild, channel, parent
            number = data.get("number")
            if type(number) is not int or number < 1:
                number = counters.get(key, 0) + 1
                data["number"] = number
            counters[key] = max(counters.get(key, 0), number)
            numbers[token] = number
            changed.append((token, parent, data, raw))
        for token, parent, data, raw in changed:
            if parent in numbers:
                data["parent_number"] = numbers[parent]
            encoded = json.dumps(data)
            if encoded != raw:
                await self.execute(
                    "UPDATE confession_posts SET data=? WHERE id=?", (encoded, token)
                )

    async def close(self):
        await self.db.close()

    async def execute(self, sql, args=(), many=False):
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
                    await self.execute("SELECT pg_advisory_xact_lock(?)", (1264141157,))
                yield
                await self.execute("COMMIT")
            except BaseException:
                await asyncio.shield(self.execute("ROLLBACK"))
                raise

    async def _config(self, guild, channel):
        row = await self.execute(
            "SELECT revision,config FROM confession_settings WHERE guild=? AND channel=?",
            (guild, channel),
        )
        return (row[0], defaults() | json.loads(row[1])) if row else (None, defaults())

    async def config(self, guild, channel):
        async with self.lock:
            return await self._config(guild, channel)

    async def channels(self, guild=None):
        async with self.lock:
            sql = "SELECT guild,channel,config FROM confession_settings"
            rows = await self.execute(
                sql + (" WHERE guild=?" if guild else ""), (guild,) if guild else (), many=True
            )
            return [(g, c, defaults() | json.loads(data)) for g, c, data in rows]

    async def configure(self, guild, channel, revision, **changes):
        async with self.transaction():
            old, config = await self._config(guild, channel)
            if old != revision:
                raise ValueError(
                    "Another administrator changed this channel. Refresh and try again."
                )
            config = validate(config | changes)
            revision = uuid.uuid4().hex
            await self.execute(
                (
                    "INSERT INTO confession_settings(guild,channel,revision,config) "
                    "VALUES(?,?,?,?) ON CONFLICT(guild,channel) DO UPDATE SET "
                    "revision=excluded.revision,config=excluded.config"
                ),
                (guild, channel, revision, json.dumps(config)),
            )
            return revision, config

    async def _key(self, guild, member):
        await self.execute(
            (
                "INSERT INTO confession_secrets(guild,secret) VALUES(?,?) ON "
                "CONFLICT(guild) DO NOTHING"
            ),
            (guild, secrets.token_hex(32)),
        )
        row = await self.execute("SELECT secret FROM confession_secrets WHERE guild=?", (guild,))
        return hmac.new(bytes.fromhex(row[0]), str(member).encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def unpack(row):
        if row is None:
            return None
        keys = (
            "id",
            "operation",
            "guild",
            "channel",
            "author",
            "author_key",
            "body",
            "parent",
            "state",
            "created",
            "data",
        )
        result = dict(zip(keys, row))
        result["data"] = json.loads(result["data"])
        return result

    async def post(self, token):
        async with self.lock:
            return self.unpack(
                await self.execute("SELECT * FROM confession_posts WHERE id=?", (token,))
            )

    async def submit(self, guild, channel, author, roles, body, operation, revision, parent=None):
        async with self.transaction():
            existing = self.unpack(
                await self.execute("SELECT * FROM confession_posts WHERE operation=?", (operation,))
            )
            if existing:
                return existing
            current, config = await self._config(guild, channel)
            if current != revision:
                raise ValueError(
                    "Channel settings changed. Reopen the form to review its privacy settings."
                )
            if not config["enabled"]:
                raise ValueError("Confessions are paused in this channel.")
            if set(config["blocked_roles"]) & set(roles) or (
                config["allowed_roles"] and not set(config["allowed_roles"]) & set(roles)
            ):
                raise ValueError("Your roles do not allow confessions in this channel.")
            body = body.strip()
            if not body or len(body) > config["max_length"]:
                raise ValueError(f"Use 1–{config['max_length']} characters.")
            key = await self._key(guild, author)
            if config["bans"] and await self.execute(
                "SELECT reason FROM confession_bans WHERE guild=? AND author_key=?", (guild, key)
            ):
                raise ValueError("You are restricted from using confessions in this server.")
            now = time.time()
            last = await self.execute(
                (
                    "SELECT created FROM confession_posts WHERE guild=? AND channel=? AND "
                    "author_key=? ORDER BY created DESC LIMIT 1"
                ),
                (guild, channel, key),
            )
            if last and now - last[0] < config["cooldown"]:
                raise ValueError("Please wait before submitting another confession or reply.")
            if parent:
                source = self.unpack(
                    await self.execute("SELECT * FROM confession_posts WHERE id=?", (parent,))
                )
                if (
                    not config["replies"]
                    or not source
                    or source["guild"] != guild
                    or source["channel"] != channel
                    or source["state"] != "published"
                ):
                    raise ValueError("This confession is not available for replies.")
            if parent:
                count = await self.execute(
                    "SELECT COUNT(*) FROM confession_posts WHERE parent=?", (parent,)
                )
            else:
                count = await self.execute(
                    "SELECT COUNT(*) FROM confession_posts WHERE guild=? AND channel=? "
                    "AND parent IS NULL",
                    (guild, channel),
                )
            token = uuid.uuid4().hex
            data = {
                "number": count[0] + 1,
                "logging": config["logging"],
                "approval": config["approval"],
                "message": 0,
                "queue": 0,
                "log": 0,
            }
            if parent:
                data["parent_number"] = source["data"]["number"]
            await self.execute(
                "INSERT INTO confession_posts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    token,
                    operation,
                    guild,
                    channel,
                    author if config["logging"] or config["approval"] else None,
                    key,
                    body,
                    parent,
                    "pending" if config["approval"] else "ready",
                    now,
                    json.dumps(data),
                ),
            )
            return self.unpack(
                await self.execute("SELECT * FROM confession_posts WHERE id=?", (token,))
            )

    async def transition(self, token, expected, state, **data):
        async with self.transaction():
            post = self.unpack(
                await self.execute("SELECT * FROM confession_posts WHERE id=?", (token,))
            )
            if post is None or post["state"] not in expected:
                raise ValueError(
                    "This submission has already been handled or changed. Refresh the queue."
                )
            post["data"].update(data)
            await self.execute(
                "UPDATE confession_posts SET state=?,data=? WHERE id=?",
                (state, json.dumps(post["data"]), token),
            )
            if state in {"rejected", "removed"} or (
                state == "published" and not post["data"]["logging"]
            ):
                await self.execute("UPDATE confession_posts SET author=NULL WHERE id=?", (token,))
            return post | {"state": state}

    async def set_ban(self, guild, moderator, reason, *, member=None, token=None, remove=False):
        async with self.transaction():
            if member:
                key = await self._key(guild, member)
            else:
                post = self.unpack(
                    await self.execute("SELECT * FROM confession_posts WHERE id=?", (token,))
                )
                if not post or post["guild"] != guild:
                    raise ValueError("Choose a confession in this server.")
                key = post["author_key"]
            if remove:
                await self.execute(
                    "DELETE FROM confession_bans WHERE guild=? AND author_key=?", (guild, key)
                )
            else:
                await self.execute(
                    (
                        "INSERT INTO confession_bans VALUES(?,?,?,?) ON "
                        "CONFLICT(guild,author_key) DO UPDATE SET "
                        "reason=excluded.reason,moderator=excluded.moderator"
                    ),
                    (guild, key, reason[:400], moderator),
                )

    async def pending(self, guild, channel):
        async with self.lock:
            rows = await self.execute(
                (
                    "SELECT * FROM confession_posts WHERE guild=? AND channel=? AND "
                    "state='pending' ORDER BY created LIMIT 25"
                ),
                (guild, channel),
                many=True,
            )
            return [self.unpack(row) for row in rows]

    async def report(self, guild, token, reporter, reason):
        async with self.transaction():
            post = self.unpack(
                await self.execute("SELECT * FROM confession_posts WHERE id=?", (token,))
            )
            if not post or post["guild"] != guild or post["state"] != "published":
                raise ValueError("This confession is no longer available.")
            if await self.execute(
                "SELECT id FROM confession_reports WHERE post=? AND reporter=?", (token, reporter)
            ):
                raise ValueError("You have already reported this confession.")
            report = uuid.uuid4().hex
            await self.execute(
                "INSERT INTO confession_reports VALUES(?,?,?,?,?,?)",
                (report, guild, token, reporter, reason[:1000], "open"),
            )
            return report

    async def panel(self, guild, channel, *, message=None, count=False, clear=False):
        async with self.transaction():
            await self.execute(
                (
                    "INSERT INTO confession_panels(guild,channel) VALUES(?,?) ON "
                    "CONFLICT(guild,channel) DO NOTHING"
                ),
                (guild, channel),
            )
            row = await self.execute(
                "SELECT message,count,retired FROM confession_panels WHERE guild=? AND channel=?",
                (guild, channel),
            )
            old, number, retired = row[0], row[1], json.loads(row[2])
            if message is not None and old and old != message:
                retired.append(old)
            await self.execute(
                (
                    "UPDATE confession_panels SET message=?,count=?,retired=? WHERE "
                    "guild=? AND channel=?"
                ),
                (
                    old if message is None else message,
                    0 if clear else number + int(count),
                    json.dumps(list(dict.fromkeys(retired))),
                    guild,
                    channel,
                ),
            )
            return old if message is None else message, 0 if clear else number + int(count), retired

    async def retired_done(self, guild, channel, message):
        async with self.transaction():
            row = await self.execute(
                "SELECT retired FROM confession_panels WHERE guild=? AND channel=?",
                (guild, channel),
            )
            if row:
                await self.execute(
                    "UPDATE confession_panels SET retired=? WHERE guild=? AND channel=?",
                    (json.dumps([v for v in json.loads(row[0]) if v != message]), guild, channel),
                )
