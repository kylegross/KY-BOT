"""Guild-scoped settings, ticket reservations and retryable delivery records."""

import copy
import json
import time
import uuid

from ky_bot.askdm.store import Store as Connection

SCHEMA = (
    (
        "CREATE TABLE IF NOT EXISTS ticket_settings (guild BIGINT PRIMARY "
        "KEY, revision TEXT NOT NULL, data TEXT NOT NULL)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS tickets (id TEXT PRIMARY KEY, guild "
        "BIGINT NOT NULL, owner BIGINT NOT NULL, channel BIGINT NOT NULL "
        "DEFAULT 0, state TEXT NOT NULL, created DOUBLE PRECISION NOT "
        "NULL, data TEXT NOT NULL)"
    ),
    "CREATE INDEX IF NOT EXISTS tickets_guild_owner ON tickets(guild,owner,state)",
    "CREATE INDEX IF NOT EXISTS tickets_channel ON tickets(guild,channel)",
    (
        "CREATE TABLE IF NOT EXISTS ticket_events (id TEXT PRIMARY KEY, "
        "ticket TEXT NOT NULL, destination BIGINT NOT NULL, delivered "
        "BIGINT NOT NULL DEFAULT 0, created DOUBLE PRECISION NOT NULL, "
        "data TEXT NOT NULL, retry_at DOUBLE PRECISION NOT NULL DEFAULT 0)"
    ),
    "CREATE INDEX IF NOT EXISTS ticket_events_pending ON ticket_events(delivered,created)",
    (
        "CREATE TABLE IF NOT EXISTS ticket_archives (id TEXT PRIMARY KEY, "
        "guild BIGINT NOT NULL, created DOUBLE PRECISION NOT NULL, body "
        "TEXT NOT NULL)"
    ),
)


def defaults():
    return dict(
        enabled=False,
        panels={},
        types={},
        defaults=dict(
            support_roles=[],
            manager_roles=[],
            log_channels=[],
            transcript_channels=[],
            category=0,
            allowed_roles=[],
            blocked_roles=[],
            cooldown=60,
            max_open=2,
            user_close=True,
            dm_transcript=False,
            feedback=True,
            ping_staff=False,
            auto_close_hours=0,
            reminder_hours=0,
            escalation_hours=0,
            support_start=0,
            support_end=24,
            timezone="America/New_York",
            retention_days=30,
            saved_replies={},
            welcome="Thanks for contacting staff. Please explain your issue below.",
            close_message="Your ticket has been closed. Thank you for contacting staff.",
        ),
    )


def new_panel(name):
    return dict(
        name=name,
        channel=0,
        published_channel=0,
        message=0,
        enabled=True,
        title="Contact Staff",
        description="Choose a topic below to open a private ticket.",
        color="BDC3CA",
        image="",
        footer="KY BOT • Private support",
        types=[],
        overrides={},
    )


def new_type(name):
    return dict(
        name=name,
        description="Contact the support team",
        emoji="🎫",
        enabled=True,
        questions=["How can we help?"],
        overrides={},
    )


def effective(config, type_id, panel_id=None):
    kind = config["types"].get(type_id)
    if not kind or not kind["enabled"]:
        raise ValueError("This ticket type is disabled or no longer exists.")
    panel = config["panels"].get(panel_id, {})
    return copy.deepcopy(config["defaults"] | panel.get("overrides", {}) | kind["overrides"])


def validate(config):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    if len(config["types"]) > 100 or len(config["panels"]) > 100:
        raise ValueError("A server can configure up to 100 panels and 100 ticket types.")
    for panel in config["panels"].values():
        if set(panel.get("overrides", {})) - {"log_channels", "transcript_channels"}:
            raise ValueError("Unknown panel override.")
        if len(panel["types"]) > 25 or set(panel["types"]) - config["types"].keys():
            raise ValueError("Choose up to 25 existing ticket types for a panel.")
        if len(panel["title"]) > 120 or len(panel["description"]) > 1500:
            raise ValueError(
                "Keep the panel title under 120 and description under 1500 characters."
            )
        try:
            if len(panel["color"]) != 6:
                raise ValueError
            int(panel["color"], 16)
        except ValueError:
            raise ValueError("Use a six-digit hex color.") from None
        if panel["image"] and not panel["image"].startswith("https://"):
            raise ValueError("Use an HTTPS image URL.")
    for kind in config["types"].values():
        if not kind["name"].strip() or len(kind["name"]) > 80:
            raise ValueError("Ticket type names must contain 1–80 characters.")
        if len(kind["questions"]) > 5 or any(
            not q.strip() or len(q) > 45 for q in kind["questions"]
        ):
            raise ValueError("Use up to five questions, each containing 1–45 characters.")
        if set(kind["overrides"]) - config["defaults"].keys():
            raise ValueError("Unknown ticket override.")
    settings = [config["defaults"]] + [
        config["defaults"] | kind["overrides"] for kind in config["types"].values()
    ]
    settings += [config["defaults"] | p.get("overrides", {}) for p in config["panels"].values()]
    for item in settings:
        for key, low, high in (
            ("cooldown", 0, 86400),
            ("max_open", 1, 20),
            ("retention_days", 1, 3650),
            ("auto_close_hours", 0, 8760),
            ("reminder_hours", 0, 8760),
            ("escalation_hours", 0, 8760),
            ("support_start", 0, 23),
            ("support_end", 1, 24),
        ):
            if not isinstance(item[key], int) or not low <= item[key] <= high:
                raise ValueError(f"{key.replace('_', ' ').title()} must be {low}–{high}.")
        if item["support_start"] >= item["support_end"]:
            raise ValueError("Support hours must start before they end; 0–24 allows all day.")
        try:
            ZoneInfo(item["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use a valid timezone such as America/New_York.") from None
        for key in (
            "log_channels",
            "transcript_channels",
            "support_roles",
            "manager_roles",
            "allowed_roles",
            "blocked_roles",
        ):
            if len(item[key]) > 10:
                raise ValueError("Select at most ten destinations or roles per setting.")
        if item["auto_close_hours"] and item["reminder_hours"] >= item["auto_close_hours"]:
            raise ValueError("The reminder must occur before automatic closure.")
        if len(item["saved_replies"]) > 25 or any(
            len(k) > 80 or not k or len(v) > 1500 or not v for k, v in item["saved_replies"].items()
        ):
            raise ValueError(
                "Use up to 25 saved replies, each with a short name and 1–1500 characters."
            )


class Store(Connection):
    @classmethod
    async def open(cls, path, database_url=None):
        # Reuse the established SQLite/Postgres connection and transaction adapter.
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
            "SELECT revision,data FROM ticket_settings WHERE guild=?", (guild,)
        )
        return (row[0], json.loads(row[1])) if row else (None, defaults())

    async def configure(self, guild, expected, config):
        validate(config)
        async with self.transaction(guild):
            revision, _ = await self._config(guild)
            if revision != expected:
                raise ValueError("Settings changed. Refresh this menu before saving again.")
            revision = uuid.uuid4().hex
            await self.execute(
                "INSERT INTO ticket_settings(guild,revision,data) VALUES (?,?,?) "
                "ON CONFLICT(guild) DO UPDATE SET revision=excluded.revision,data=excluded.data",
                (guild, revision, json.dumps(config)),
            )
        return revision

    async def all_configs(self):
        async with self.lock:
            rows = await self.execute("SELECT guild,data FROM ticket_settings", many=True)
        return [(guild, json.loads(data)) for guild, data in rows]

    def decode(self, row):
        return (
            dict(
                id=row[0],
                guild=row[1],
                owner=row[2],
                channel=row[3],
                state=row[4],
                created=row[5],
                **json.loads(row[6]),
            )
            if row
            else None
        )

    async def get(self, token):
        async with self.lock:
            return self.decode(await self.execute("SELECT * FROM tickets WHERE id=?", (token,)))

    async def listed(self, guild=None):
        async with self.lock:
            rows = await self.execute(
                "SELECT * FROM tickets" + (" WHERE guild=?" if guild else ""),
                (guild,) if guild else (),
                many=True,
            )
        return [self.decode(row) for row in rows]

    async def by_channel(self, guild, channel):
        async with self.lock:
            row = await self.execute(
                "SELECT * FROM tickets WHERE guild=? AND channel=?", (guild, channel)
            )
        return self.decode(row)

    async def reserve(
        self, guild, owner, panel_id, type_id, answers, *, now=None, expected_config=None
    ):
        now = time.time() if now is None else now
        async with self.transaction(guild):
            _, config = await self._config(guild)
            if expected_config is not None and config != expected_config:
                raise ValueError("Ticket settings changed. Reopen the panel and try again.")
            settings = effective(config, type_id, panel_id)
            panel = config["panels"].get(panel_id)
            if (
                not config["enabled"]
                or not panel
                or not panel["enabled"]
                or type_id not in panel["types"]
            ):
                raise ValueError("This ticket panel is disabled. Please use a current panel.")
            rows = await self.execute(
                "SELECT state,created FROM tickets WHERE guild=? AND owner=?",
                (guild, owner),
                many=True,
            )
            if (
                sum(
                    state in {"creating", "open", "waiting_user", "waiting_staff"}
                    for state, _ in rows
                )
                >= settings["max_open"]
            ):
                raise ValueError(
                    "You have reached the active ticket limit. Finish an existing ticket first."
                )
            if any(
                state != "failed" and now - created < settings["cooldown"]
                for state, created in rows
            ):
                raise ValueError("Please wait before opening another ticket.")
            token = uuid.uuid4().hex
            data = dict(
                panel=panel_id,
                type=type_id,
                type_name=config["types"][type_id]["name"],
                settings=settings,
                answers=answers,
                claimed=0,
                priority="Normal",
                guests=[],
                intro_sent=False,
                notes=[],
                first_response=0,
                last_activity=now,
                waiting_since=now,
                reminded=False,
                escalated=False,
                paused=False,
                closed_at=0,
                reason="",
                rating=0,
            )
            await self.execute(
                "INSERT INTO tickets VALUES (?,?,?,?,?,?,?)",
                (token, guild, owner, 0, "creating", now, json.dumps(data)),
            )
        return await self.get(token)

    async def update(self, ticket, **changes):
        async with self.transaction(ticket["guild"]):
            current = self.decode(
                await self.execute("SELECT * FROM tickets WHERE id=?", (ticket["id"],))
            )
            if current is None:
                raise ValueError("This ticket is unavailable.")
            current.update(changes)
            data = {
                k: v
                for k, v in current.items()
                if k not in {"id", "guild", "owner", "channel", "state", "created"}
            }
            await self.execute(
                "UPDATE tickets SET channel=?,state=?,data=? WHERE id=?",
                (current["channel"], current["state"], json.dumps(data), current["id"]),
            )
        return current

    async def event(self, ticket, title, actor, detail="", *, archive=False):
        destinations = ticket["settings"]["transcript_channels" if archive else "log_channels"]
        async with self.transaction(ticket["guild"]):
            for channel in dict.fromkeys(destinations):
                await self.execute(
                    (
                        "INSERT INTO "
                        "ticket_events(id,ticket,destination,delivered,created,data) "
                        "VALUES (?,?,?,?,?,?)"
                    ),
                    (
                        uuid.uuid4().hex,
                        ticket["id"],
                        channel,
                        0,
                        time.time(),
                        json.dumps(
                            dict(title=title, actor=actor, detail=detail[:1500], archive=archive)
                        ),
                    ),
                )

    async def events(self, ticket=None):
        async with self.lock:
            if ticket:
                return await self.execute(
                    (
                        "SELECT id,ticket,destination,data FROM ticket_events WHERE "
                        "delivered=0 AND ticket=?"
                    ),
                    (ticket,),
                    many=True,
                )
            return await self.execute(
                (
                    "SELECT id,ticket,destination,data FROM ticket_events WHERE "
                    "delivered=0 AND retry_at<=? ORDER BY retry_at,created LIMIT 100"
                ),
                (time.time(),),
                many=True,
            )

    async def retry(self, event):
        async with self.lock:
            await self.execute(
                "UPDATE ticket_events SET retry_at=? WHERE id=?", (time.time() + 60, event)
            )

    async def delivered(self, event):
        async with self.lock:
            await self.execute("UPDATE ticket_events SET delivered=1 WHERE id=?", (event,))

    async def pending_archives(self, ticket):
        async with self.lock:
            rows = await self.execute(
                "SELECT data FROM ticket_events WHERE ticket=? AND delivered=0",
                (ticket,),
                many=True,
            )
        return any(json.loads(row[0])["archive"] for row in rows)

    async def archive(self, ticket, body=None):
        async with self.lock:
            if body is not None:
                await self.execute(
                    (
                        "INSERT INTO ticket_archives VALUES (?,?,?,?) ON CONFLICT(id) DO "
                        "UPDATE SET created=excluded.created,body=excluded.body"
                    ),
                    (ticket["id"], ticket["guild"], time.time(), body),
                )
            row = await self.execute("SELECT body FROM ticket_archives WHERE id=?", (ticket["id"],))
        return row[0] if row else None

    async def prune(self, guild, retention):
        cutoff = time.time() - retention * 86400
        async with self.transaction(guild):
            rows = await self.execute(
                "SELECT a.id,a.created,t.data,t.state FROM ticket_archives a "
                "JOIN tickets t ON a.id=t.id WHERE a.guild=? "
                "AND a.id NOT IN (SELECT ticket FROM ticket_events WHERE delivered=0)",
                (guild,),
                many=True,
            )
            for token, created, raw, state in rows:
                data = json.loads(raw)
                if created >= time.time() - data["settings"]["retention_days"] * 86400:
                    continue
                await self.execute("DELETE FROM ticket_archives WHERE id=?", (token,))
                if state in {"closed", "deleted"}:
                    data.update(answers=[], notes=[], reason="")
                    await self.execute(
                        "UPDATE tickets SET data=? WHERE id=?", (json.dumps(data), token)
                    )
            await self.execute(
                (
                    "DELETE FROM ticket_events WHERE delivered=1 AND created<? AND "
                    "ticket IN (SELECT id FROM tickets WHERE guild=?)"
                ),
                (cutoff, guild),
            )
