"""Revision-checked setup, resumable jobs and per-worker leases (SQLite/PostgreSQL)."""

import json
import time
import uuid

from ky_bot.askdm.store import Store as Connection
from ky_bot.custom_embeds.model import defaults as panel_defaults
from ky_bot.custom_embeds.model import validate as validate_panel

SCHEMA = (
    (
        "CREATE TABLE IF NOT EXISTS media_removal_settings (guild "
        "BIGINT PRIMARY KEY, revision TEXT NOT NULL, config TEXT NOT "
        "NULL)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS media_removal_jobs (id TEXT "
        "PRIMARY KEY, guild BIGINT NOT NULL, owner BIGINT NOT NULL, "
        "cutoff BIGINT NOT NULL, state TEXT NOT NULL, created DOUBLE "
        "PRECISION NOT NULL, deleted BIGINT NOT NULL DEFAULT 0, "
        "scanned BIGINT NOT NULL DEFAULT 0, lease_until DOUBLE "
        "PRECISION NOT NULL DEFAULT 0, lease TEXT NOT NULL DEFAULT "
        "'')"
    ),
    (
        "CREATE UNIQUE INDEX IF NOT EXISTS media_removal_active ON "
        "media_removal_jobs(guild,owner) WHERE state IN "
        "('discovering','running')"
    ),
    (
        "CREATE TABLE IF NOT EXISTS media_removal_channels (job TEXT "
        "NOT NULL, channel BIGINT NOT NULL, cursor BIGINT NOT NULL, "
        "state TEXT NOT NULL DEFAULT 'pending', PRIMARY "
        "KEY(job,channel))"
    ),
    (
        "CREATE TABLE IF NOT EXISTS media_removal_issues (job TEXT "
        "NOT NULL, scope TEXT NOT NULL, PRIMARY KEY(job,scope))"
    ),
)
FIELDS = (
    "id",
    "guild",
    "owner",
    "cutoff",
    "state",
    "created",
    "deleted",
    "scanned",
    "lease_until",
    "lease",
)


def defaults():
    from ky_bot.custom_embeds.artwork import standard_image

    panel = panel_defaults() | dict(
        header=standard_image("header", "silver_neon"),
        footer=standard_image("footer", "silver_neon"),
        header_finish="silver_neon",
        footer_finish="silver_neon",
        header_title="YOUR MEDIA, YOUR CHOICE",
        text_finish="silver_neon",
        name="Media removal panel",
        title="YOUR MEDIA, YOUR CHOICE",
        title_style="decorative",
        body=(
            "**NEED A FRESH START?**\n"
            "You’re in control of what you share. If you’d like to remove your uploaded photos "
            "and videos from this server, you can start a private cleanup here.\n\n"
            "**WHAT GETS REMOVED?**\n"
            "Your posts containing uploaded images or videos, including any captions attached "
            "to those posts.\n\n"
            "**WHAT STAYS?**\n"
            "Text-only messages, GIF-only posts, audio-only posts, stickers, links, and other "
            "file-only posts."
        ),
        footer_image_text="Your content. Your choice.",
    )
    return dict(
        channel=0,
        enabled=False,
        message=0,
        published_channel=0,
        button_label="Review my media cleanup",
        button_heading="READY TO CLEAR YOUR MEDIA?",
        button_text=(
            "Select the button to review and confirm privately. Nothing is deleted "
            "until you confirm, and deletion is permanent."
        ),
        copy_version=1,
        inline=True,
        panel=panel,
    )


def upgrade_copy(config):
    """Replace untouched initial placeholder copy; preserve administrator edits."""
    result = defaults() | config
    result["panel"] = defaults()["panel"] | config.get("panel", {})
    if config.get("copy_version", 0) >= 1:
        return result
    approved = defaults()
    original_body = (
        "Remove your own uploaded images and videos from this "
        "server. You will confirm privately before anything is deleted."
    )
    original = {
        "header_title": "MEDIA REMOVAL",
        "title": "REMOVE YOUR MEDIA",
        "body": original_body,
    }
    for key, value in original.items():
        if result["panel"][key] == value:
            result["panel"][key] = approved["panel"][key]
    if (
        config.get("panel", {}).get("body") == original_body
        and not result["panel"]["footer_image_text"]
    ):
        result["panel"]["footer_image_text"] = approved["panel"]["footer_image_text"]
    for key, value in {
        "button_label": "Remove my media",
        "button_heading": "YOUR MEDIA, YOUR CHOICE",
        "button_text": "Open your private cleanup options.",
    }.items():
        if result[key] == value:
            result[key] = approved[key]
    result["copy_version"] = 1
    return result


def validate(config):
    value = defaults() | config
    if set(value) != set(defaults()):
        raise ValueError("Unknown media removal setting.")
    if type(value["copy_version"]) is not int or value["copy_version"] != 1:
        raise ValueError("Unsupported media removal copy version.")
    for key in ("channel", "message", "published_channel"):
        if type(value[key]) is not int or not 0 <= value[key] < 2**63:
            raise ValueError("Choose a valid channel.")
    for key in ("enabled", "inline"):
        if type(value[key]) is not bool:
            raise ValueError("Choose on or off.")
    if value["enabled"] and not value["channel"]:
        raise ValueError("Choose the panel channel before enabling media removal.")
    for key, limit in (("button_label", 80), ("button_heading", 80), ("button_text", 1000)):
        if not isinstance(value[key], str) or len(value[key]) > limit:
            raise ValueError(f"{key.replace('_', ' ')} must be at most {limit} characters.")
    if not value["button_label"].strip() or not value["button_text"].strip():
        raise ValueError("The removal button needs a label and description.")
    value["panel"] = validate_panel(value["panel"])
    value["panel"]["channel"] = value["channel"]
    value["panel"]["sticky_every"] = 0
    return value


class Store(Connection):
    async def execute(self, sql, args=(), *, many=False):
        cursor = await self.db.execute(sql.replace("?", "%s") if self.postgres else sql, args)
        try:
            if sql.lstrip().upper().startswith("SELECT") or "RETURNING" in sql.upper():
                return await cursor.fetchall() if many else await cursor.fetchone()
        finally:
            await cursor.close()

    @classmethod
    async def open(cls, path, database_url=None):
        store = await super().open(path, database_url)
        try:
            async with store.transaction(0):
                for sql in SCHEMA:
                    await store.execute(sql)
        except BaseException:
            await store.close()
            raise
        return store

    async def _config(self, guild):
        row = await self.execute(
            "SELECT revision,config FROM media_removal_settings WHERE guild=?", (guild,)
        )
        return (row[0], upgrade_copy(json.loads(row[1]))) if row else (None, defaults())

    async def config(self, guild):
        async with self.lock:
            return await self._config(guild)

    async def configure(self, guild, revision, **changes):
        async with self.transaction(guild):
            current, config = await self._config(guild)
            if current != revision:
                raise ValueError("Settings changed. Refresh the media removal menu.")
            old_channel = config["channel"]
            config = validate(config | changes)
            revision = uuid.uuid4().hex
            await self.execute(
                (
                    "INSERT INTO media_removal_settings(guild,revision,config) "
                    "VALUES (?,?,?) ON CONFLICT(guild) DO UPDATE SET "
                    "revision=excluded.revision,config=excluded.config"
                ),
                (guild, revision, json.dumps(config)),
            )
            if not config["enabled"] or config["channel"] != old_channel:
                await self.execute(
                    (
                        "UPDATE media_removal_jobs SET state='paused' WHERE guild=? "
                        "AND state IN ('discovering','running')"
                    ),
                    (guild,),
                )
        return revision, config

    async def _job(self, key):
        row = await self.execute(
            "SELECT " + ",".join(FIELDS) + " FROM media_removal_jobs WHERE id=?", (key,)
        )
        return dict(zip(FIELDS, row)) if row else None

    async def job(self, key, *, guild=None, owner=None):
        async with self.lock:
            job = await self._job(key)
        if job and (
            (guild is not None and job["guild"] != guild)
            or (owner is not None and job["owner"] != owner)
        ):
            raise ValueError("Open your own media cleanup status.")
        return job

    async def latest(self, guild, owner):
        async with self.lock:
            row = await self.execute(
                (
                    "SELECT id FROM media_removal_jobs WHERE guild=? AND owner=? "
                    "ORDER BY created DESC LIMIT 1"
                ),
                (guild, owner),
            )
            return await self._job(row[0]) if row else None

    async def request(self, guild, owner, cutoff, expected):
        async with self.transaction(guild):
            revision, config = await self._config(guild)
            if revision != expected or not config["enabled"] or not config["channel"]:
                raise ValueError(
                    "This setup changed or is disabled. Open the media removal panel again."
                )
            row = await self.execute(
                (
                    "SELECT id FROM media_removal_jobs WHERE guild=? AND owner=? "
                    "AND state IN ('discovering','running')"
                ),
                (guild, owner),
            )
            if row:
                return await self._job(row[0])
            key = uuid.uuid4().hex
            await self.execute(
                (
                    "INSERT INTO "
                    "media_removal_jobs(id,guild,owner,cutoff,state,created) "
                    "VALUES (?,?,?,?,?,?)"
                ),
                (key, guild, owner, cutoff, "discovering", time.time()),
            )
            return await self._job(key)

    async def active(self):
        async with self.lock:
            return await self.execute(
                (
                    "SELECT id,guild FROM media_removal_jobs WHERE state IN "
                    "('discovering','running') ORDER BY created LIMIT 50"
                ),
                many=True,
            )

    async def claim(self, key, token):
        async with self.transaction(0):
            row = await self.execute(
                (
                    "UPDATE media_removal_jobs SET lease=?,lease_until=? WHERE "
                    "id=? AND state IN ('discovering','running') AND "
                    "lease_until<? RETURNING id"
                ),
                (token, time.time() + 120, key, time.time()),
            )
            return await self._job(key) if row else None

    async def touch(self, key, token):
        async with self.transaction(0):
            return bool(
                await self.execute(
                    (
                        "UPDATE media_removal_jobs SET lease_until=? WHERE id=? AND "
                        "lease=? AND state IN ('discovering','running') RETURNING id"
                    ),
                    (time.time() + 120, key, token),
                )
            )

    async def release(self, key, token):
        async with self.transaction(0):
            await self.execute(
                "UPDATE media_removal_jobs SET lease='',lease_until=0 WHERE id=? AND lease=?",
                (key, token),
            )

    async def add_channel(self, job, channel):
        async with self.transaction(job["guild"]):
            await self.execute(
                (
                    "INSERT INTO media_removal_channels(job,channel,cursor) "
                    "VALUES (?,?,?) ON CONFLICT(job,channel) DO NOTHING"
                ),
                (job["id"], channel, job["cutoff"]),
            )

    async def issue(self, key, scope):
        async with self.transaction(0):
            await self.execute(
                (
                    "INSERT INTO media_removal_issues(job,scope) VALUES (?,?) ON "
                    "CONFLICT(job,scope) DO NOTHING"
                ),
                (key, scope),
            )

    async def state(self, job, state):
        async with self.transaction(job["guild"]):
            await self.execute(
                (
                    "UPDATE media_removal_jobs SET state=? WHERE id=? AND "
                    "lease=? AND state IN ('discovering','running')"
                ),
                (state, job["id"], job["lease"]),
            )

    async def next_channel(self, key):
        async with self.lock:
            return await self.execute(
                (
                    "SELECT channel,cursor FROM media_removal_channels WHERE "
                    "job=? AND state='pending' ORDER BY channel LIMIT 1"
                ),
                (key,),
            )

    async def page_done(self, job, channel, cursor, scanned, *, state="pending"):
        async with self.transaction(job["guild"]):
            current = await self._job(job["id"])
            if current["lease"] != job["lease"] or current["state"] not in (
                "discovering",
                "running",
            ):
                return
            await self.execute(
                "UPDATE media_removal_channels SET cursor=?,state=? WHERE job=? AND channel=?",
                (cursor, state, job["id"], channel),
            )
            await self.execute(
                "UPDATE media_removal_jobs SET scanned=scanned+? WHERE id=?", (scanned, job["id"])
            )

    async def deleted(self, job):
        async with self.transaction(job["guild"]):
            await self.execute(
                "UPDATE media_removal_jobs SET deleted=deleted+1 WHERE id=? AND lease=?",
                (job["id"], job["lease"]),
            )

    async def issues(self, key):
        async with self.lock:
            row = await self.execute(
                "SELECT COUNT(*) FROM media_removal_issues WHERE job=?", (key,)
            )
            return row[0]
