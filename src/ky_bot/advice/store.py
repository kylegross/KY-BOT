"""Advice storage, isolated from Confessions, with revision and state checks."""

import hmac
import json
import time
import uuid

from ky_bot.confessions.store import SCHEMA
from ky_bot.confessions.store import Store as ConfessionStore
from ky_bot.confessions.store import defaults as confession_defaults
from ky_bot.confessions.store import validate as validate_confession


def defaults():
    return confession_defaults() | dict(
        header_title="ASK FOR ADVICE",
        title="ANONYMOUS QUESTION",
        panel_title="ASK FOR ADVICE",
        panel_text="Have a question or need another perspective? Ask anonymously here.",
        submit_label="Ask anonymously",
        footer_text="",
        sticky=True,
        access_channel=0,
        access_role=0,
        advice_role=0,
        response_approval=False,
        notifications=False,
        response_title="ADVICE",
        header_text_color=None,
        header_text_finish=None,
        sections=[
            dict(
                heading="ASK FOR ADVICE",
                decorated=True,
                rows=[
                    dict(
                        action="join",
                        label="Join",
                        emoji="🚪",
                        text=(
                            "Need another perspective or someone to hear you out? "
                            "Join to ask anonymously and read advice from the community."
                        ),
                    ),
                    dict(
                        action="leave",
                        label="Leave",
                        emoji="",
                        text=(
                            "Want to leave? Remove your channel access and your advice role, "
                            "if you have one."
                        ),
                    ),
                ],
            ),
            dict(
                heading="WANT TO HELP?",
                decorated=False,
                rows=[
                    dict(
                        action="give",
                        label="Give Advice",
                        emoji="💬",
                        text=(
                            "Once you’ve joined, opt in to give advice. Respond anonymously "
                            "or choose to show your name."
                        ),
                    ),
                    dict(
                        action="ungive",
                        label="Remove Advice Role",
                        emoji="",
                        text=(
                            "No longer want to give advice? Remove your advice role while "
                            "keeping your access to the channel."
                        ),
                    ),
                ],
            ),
        ],
    )


def validate(config):
    result = defaults() | config
    if set(result) != set(defaults()):
        raise ValueError("Unknown advice setting.")
    base = {key: result[key] for key in confession_defaults()}
    # The same private queue serves two independently approved submission types.
    if result["response_approval"] and not result["queue_channel"]:
        raise ValueError("Choose a private approval destination first.")
    result.update(validate_confession(base))
    from ky_bot.design.menu_design import FINISHES
    from ky_bot.design.text_colour import text_colour

    result["header_text_color"] = text_colour(result["header_text_color"])
    if result["header_text_finish"] is not None and result["header_text_finish"] not in FINISHES:
        raise ValueError("Choose a standard header title finish or follow decorative text.")
    if (
        not isinstance(result["response_title"], str)
        or not 1 <= len(result["response_title"]) <= 80
    ):
        raise ValueError("Use 1–80 characters for the advice response heading.")
    for key in ("response_approval", "notifications"):
        if type(result[key]) is not bool:
            raise ValueError("Choose on or off.")
    for key in ("access_channel", "access_role", "advice_role"):
        if type(result[key]) is not int or not 0 <= result[key] < 2**63:
            raise ValueError("Choose a valid channel or role.")
    if result["access_role"] and result["access_role"] == result["advice_role"]:
        raise ValueError("Access and advice must use different roles.")
    if result["enabled"] and not all(
        result[k] for k in ("access_channel", "access_role", "advice_role")
    ):
        raise ValueError("Choose both channels and both roles before enabling advice.")
    sections = result["sections"]
    if not isinstance(sections, list) or not 1 <= len(sections) <= 4:
        raise ValueError("Use 1–4 panel sections.")
    actions, characters, rows = [], 0, 0
    for section in sections:
        if not isinstance(section, dict) or set(section) != {"heading", "decorated", "rows"}:
            raise ValueError("Each section needs heading, decorated, and rows fields.")
        if not isinstance(section["heading"], str) or len(section["heading"]) > 80:
            raise ValueError("Section headings must be at most 80 characters.")
        if type(section["decorated"]) is not bool or not isinstance(section["rows"], list):
            raise ValueError("Use a boolean decorated value and a list of rows.")
        if not 1 <= len(section["rows"]) <= 4:
            raise ValueError("Each section needs 1–4 rows.")
        for row in section["rows"]:
            if not isinstance(row, dict) or set(row) != {"action", "label", "emoji", "text"}:
                raise ValueError("Each row needs action, label, emoji, and text fields.")
            if row["action"] not in {"join", "leave", "give", "ungive", "none"}:
                raise ValueError("Choose join, leave, give, ungive, or none for row actions.")
            for key, limit in (("label", 80), ("emoji", 100), ("text", 1000)):
                if not isinstance(row[key], str) or len(row[key]) > limit:
                    raise ValueError(f"Row {key} must be at most {limit} characters.")
            if not row["text"].strip() or (row["action"] != "none" and not row["label"].strip()):
                raise ValueError("Rows need text; button rows also need a label.")
            if row["emoji"]:
                import discord

                discord.PartialEmoji.from_str(row["emoji"])
            if row["action"] != "none":
                actions.append(row["action"])
            characters += len(row["text"])
            rows += 1
    if len(actions) != len(set(actions)):
        raise ValueError("Each role action may appear only once.")
    if characters > 2800 or rows > 6:
        raise ValueError("Keep the panel within 6 rows and 2,800 text characters.")
    return result


class Store(ConfessionStore):
    """Reuse the tested connection/transaction primitives with separate advice tables."""

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

    async def execute(self, sql, args=(), many=False):
        return await super().execute(sql.replace("confession_", "advice_"), args, many)

    async def _config(self, guild, channel):
        row = await self.execute(
            "SELECT revision,config FROM advice_settings WHERE guild=? AND channel=?",
            (guild, channel),
        )
        return (row[0], defaults() | json.loads(row[1])) if row else (None, defaults())

    async def channels(self, guild=None):
        async with self.lock:
            rows = await self.execute(
                "SELECT guild,channel,config FROM advice_settings"
                + (" WHERE guild=?" if guild else ""),
                (guild,) if guild else (),
                many=True,
            )
            return [(g, c, defaults() | json.loads(data)) for g, c, data in rows]

    async def configure(self, guild, channel, revision, **changes):
        async with self.transaction():
            current, config = await self._config(guild, channel)
            if current != revision:
                raise ValueError("Settings changed. Refresh this menu and try again.")
            config = validate(config | changes)
            if config["access_channel"] == channel:
                raise ValueError("Use separate access and ask channels.")
            revision = uuid.uuid4().hex
            await self.execute(
                "INSERT INTO advice_settings VALUES(?,?,?,?) ON CONFLICT(guild,channel) "
                "DO UPDATE SET revision=excluded.revision,config=excluded.config",
                (guild, channel, revision, json.dumps(config)),
            )
            return revision, config

    async def submit(
        self, guild, channel, author, roles, body, operation, revision, parent=None, named=False
    ):
        async with self.transaction():
            existing = self.unpack(
                await self.execute("SELECT * FROM advice_posts WHERE operation=?", (operation,))
            )
            if existing:
                return existing
            current, config = await self._config(guild, channel)
            if revision != current:
                raise ValueError("Settings changed. Reopen the form to review its privacy notice.")
            if not config["enabled"] or config["access_role"] not in roles:
                raise ValueError("Join Ask for Advice before submitting.")
            if not isinstance(body, str) or not body.strip() or len(body) > config["max_length"]:
                raise ValueError(f"Use 1–{config['max_length']} characters.")
            source = None
            reply_to = parent
            if parent:
                source = await self._root(parent, guild, channel)
                parent = source["id"]
            key = await self._key(guild, author)
            is_original = bool(source and hmac.compare_digest(source["author_key"], key))
            if parent and not is_original and config["advice_role"] not in roles:
                raise ValueError("Choose Give Advice before responding to questions.")
            now = time.time()
            last = await self.execute(
                "SELECT created FROM advice_posts WHERE guild=? AND channel=? AND author_key=? "
                "ORDER BY created DESC LIMIT 1",
                (guild, channel, key),
            )
            if last and now - last[0] < config["cooldown"]:
                raise ValueError("Please wait before submitting again.")
            count = await self.execute(
                "SELECT COUNT(*) FROM advice_posts WHERE guild=? AND channel=? AND parent IS NULL",
                (guild, channel),
            )
            approval = config["response_approval"] if parent else config["approval"]
            data = dict(
                number=source["data"]["number"] if source else count[0] + 1,
                logging=config["logging"],
                approval=approval,
                public_author=author if parent and named else None,
                is_original_poster=is_original,
                reply_to=reply_to,
                queue_channel=config["queue_channel"],
                log_channel=config["log_channel"],
                message=0,
                queue=0,
                log=0,
            )
            token = uuid.uuid4().hex
            await self.execute(
                "INSERT INTO advice_posts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    token,
                    operation,
                    guild,
                    channel,
                    author if approval or config["logging"] else None,
                    key,
                    body.strip(),
                    parent,
                    "pending" if approval else "ready",
                    now,
                    json.dumps(data),
                ),
            )
            return self.unpack(
                await self.execute("SELECT * FROM advice_posts WHERE id=?", (token,))
            )

    async def work(self, guild=None):
        async with self.lock:
            rows = await self.execute(
                "SELECT * FROM advice_posts WHERE "
                "state IN ('ready','pending','sending','published')"
                + (" AND guild=?" if guild else "")
                + " ORDER BY created",
                (guild,) if guild else (),
                many=True,
            )
            return [self.unpack(row) for row in rows]
