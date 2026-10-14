"""Privacy-preserving activity logging for AutoDelete channels."""

import asyncio
import datetime as dt
import json
import logging
import re
import time
import uuid
from urllib.parse import urlparse

import discord

RETENTION_SECONDS = 15 * 24 * 60 * 60
DESTINATION = 0
DEFAULTS = {}
TYPES = {
    "messages": "Messages / captions",
    "gifs": "GIF links (no previews)",
    "media": "Image / video upload notices (no media)",
    "edits": "Message edits",
    "deletes": "Message deletions",
    "reaction_remove": "Reactions removed",
    "reaction_clear": "Reactions cleared",
}
URL = re.compile(r"https?://[^\s<>]+", re.I)
MEDIA_EXT = re.compile(r"\.(?:png|jpe?g|webp|avif|bmp|heic|mp4|mov|webm|mkv)(?:[?#]|$)", re.I)

CUSTOM_EMOJI = re.compile(r"(<a?:[A-Za-z0-9_]{2,32}:[0-9]{17,20}>)")


def safe(value):
    # Keep Discord's custom emoji syntax intact, including underscores in names.
    # Escape surrounding member text and mentions without altering Unicode emoji.
    return "".join(
        part
        if CUSTOM_EMOJI.fullmatch(part)
        else discord.utils.escape_mentions(discord.utils.escape_markdown(part))
        for part in CUSTOM_EMOJI.split(str(value))
    )


def gif_host(url):
    return (urlparse(url).hostname or "").lower().removeprefix("www.") in {
        "tenor.com",
        "giphy.com",
        "media.giphy.com",
    }


def snapshot(message, names=None):
    """Never retain image/video URLs, filenames, bytes, previews, or embed bodies."""
    links = []
    blocked = set()
    kinds = []
    for a in getattr(message, "attachments", []):
        mime = (a.content_type or "").lower()
        name = a.filename.lower()
        url = str(a.url)
        if mime == "image/gif" or name.endswith(".gif"):
            kinds.append("GIF")
            links.append(url)
        else:
            blocked.add(url)
            if getattr(a, "proxy_url", None):
                blocked.add(str(a.proxy_url))
            kinds.append(
                "video"
                if mime.startswith("video/") or name.endswith((".mp4", ".mov", ".webm", ".mkv"))
                else "image"
                if mime.startswith("image/")
                or name.endswith((".png", ".jpg", ".jpeg", ".webp", ".avif", ".heic"))
                else "attachment"
            )
    for e in getattr(message, "embeds", []):
        if e.url and gif_host(e.url):
            links.append(e.url)
            kinds.append("GIF")
        else:
            media = [
                getattr(e.image, "url", None),
                getattr(e.video, "url", None),
                getattr(e.thumbnail, "url", None),
            ]
            if e.type in ("image", "video", "gifv") or any(media):
                if e.url:
                    blocked.add(e.url)
                blocked.update(x for x in media if x)
                kinds.append("video" if e.type in ("video", "gifv") else "image")
    content = message.content or ""
    for url in URL.findall(content):
        if gif_host(url):
            links.append(url)
            kinds.append("GIF")
    links = list(dict.fromkeys(links))

    def redact(match):
        url = match.group()
        host = (urlparse(url).hostname or "").lower()
        if url in links:
            return "[GIF link below]"
        if (
            url in blocked
            or MEDIA_EXT.search(url)
            or host in {"cdn.discordapp.com", "media.discordapp.net"}
        ):
            return "[private media link omitted]"
        return "[link omitted to protect media privacy]"

    content = URL.sub(redact, content)
    known = {
        u.id: u.display_name for u in getattr(message, "mentions", []) if hasattr(u, "display_name")
    }
    known.update(names or {})

    def mention(match):
        uid = int(match.group(1))
        name = known.get(uid)
        lookup = getattr(message.guild, "get_member", None)
        member = lookup(uid) if lookup else None
        return (
            name or (member.display_name if member else "Unknown member")
        ) + f" (User ID: {uid})"

    content = re.sub(r"<@!?(\d+)>", mention, content)
    roles = {role.id: role.name for role in getattr(message, "role_mentions", [])}

    def role_mention(match):
        rid = int(match.group(1))
        name = roles.get(rid)
        lookup = getattr(message.guild, "get_role", None)
        role = lookup(rid) if lookup else None
        return "@" + (name or (role.name if role else "Unknown role"))

    content = re.sub(r"<@&(\d+)>", role_mention, content)
    author = message.author
    return dict(
        user=author.id,
        name=author.display_name,
        text=content,
        kinds=sorted(set(kinds)),
        gifs=links,
        created=message.created_at.timestamp(),
    )


def body(s):
    parts = [s["text"]] if s.get("text") else []
    if s.get("kinds"):
        labels = {
            "image": "an image",
            "video": "a video",
            "GIF": "a GIF",
            "attachment": "an attachment",
        }
        parts.append("Sent " + ", ".join(labels.get(k, k) for k in s["kinds"]) + ".")
        if any(k in s["kinds"] for k in ("image", "video")):
            parts.append("-# Images/videos are not retained.")
    if not parts:
        parts.append("Sent a message without text.")
    return "\n".join(parts)


class ActivityLogs:
    def __init__(self, client, store):
        self.client = client
        self.store = store
        self.db = store.db
        self.task = None
        store.activity_logs = self
        with self.db:
            self.db.executescript("""
            CREATE TABLE IF NOT EXISTS activity_log_links(guild INTEGER,message INTEGER,root INTEGER,context TEXT,PRIMARY KEY(guild,message));
            CREATE TABLE IF NOT EXISTS activity_log_batches(id TEXT PRIMARY KEY,guild INTEGER,source INTEGER,destination INTEGER,member TEXT,started REAL);
            CREATE INDEX IF NOT EXISTS activity_log_batch_lookup ON activity_log_batches(guild,source,destination,member,started);
            CREATE TABLE IF NOT EXISTS activity_log_events(token TEXT PRIMARY KEY,batch TEXT,body TEXT);
            CREATE TABLE IF NOT EXISTS activity_log_retention(message INTEGER PRIMARY KEY,guild INTEGER,destination INTEGER,expires REAL,retry REAL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS activity_log_config(guild INTEGER,source INTEGER,destination INTEGER,title TEXT,color INTEGER,types TEXT,enabled INTEGER DEFAULT 1,PRIMARY KEY(guild,source));
            CREATE TABLE IF NOT EXISTS activity_log_reactions(key TEXT PRIMARY KEY,data TEXT);
            CREATE TABLE IF NOT EXISTS activity_log_deletions(guild INTEGER,message INTEGER,status TEXT,PRIMARY KEY(guild,message));
            CREATE TABLE IF NOT EXISTS activity_log_seed(guild INTEGER PRIMARY KEY);
            CREATE TABLE IF NOT EXISTS activity_log_messages(guild INTEGER,channel INTEGER,message INTEGER,data TEXT,PRIMARY KEY(guild,message));
            CREATE TABLE IF NOT EXISTS activity_log_queue(id TEXT PRIMARY KEY,guild INTEGER,destination INTEGER,payload TEXT,created REAL,state TEXT DEFAULT 'pending',sending REAL,message INTEGER,retry REAL DEFAULT 0);
            """)
        with self.db:
            for row in self.db.execute(
                "SELECT guild,destination,message FROM activity_log_queue WHERE message IS NOT NULL"
            ).fetchall():
                self.track_retention(row["guild"], row["destination"], row["message"])
            for row in self.db.execute(
                "SELECT guild,source,types FROM activity_log_config"
            ).fetchall():
                types = [t for t in json.loads(row["types"]) if t in TYPES]
                self.db.execute(
                    "UPDATE activity_log_config SET types=?,enabled=CASE WHEN ?=0 THEN 0 ELSE enabled END WHERE guild=? AND source=?",
                    (json.dumps(types), len(types), row["guild"], row["source"]),
                )

    def active_channels(self, guild):
        return {r["channel"] for r in self.store.list(guild) if r["enabled"]}

    def config(self, guild, channel):
        row = self.db.execute(
            "SELECT * FROM activity_log_config WHERE guild=? AND source=?", (guild, channel)
        ).fetchone()
        return dict(row) if row else None

    def seed(self, guild):
        if self.db.execute("SELECT 1 FROM activity_log_seed WHERE guild=?", (guild.id,)).fetchone():
            return
        if not guild.get_channel(DESTINATION):
            return
        with self.db:
            for source, (title, color) in DEFAULTS.items():
                if guild.get_channel(source):
                    self.db.execute(
                        "INSERT OR IGNORE INTO activity_log_config VALUES (?,?,?,?,?,?,1)",
                        (guild.id, source, DESTINATION, title, color, json.dumps(list(TYPES))),
                    )
            self.db.execute("INSERT OR IGNORE INTO activity_log_seed VALUES (?)", (guild.id,))

    def route(self, guild, channel):
        g = self.client.get_guild(guild)
        if g:
            self.seed(g)
        row = self.config(guild, channel)
        return row if row and row["enabled"] and channel in self.active_channels(guild) else None

    def save(self, guild, source, destination, types, title=None, color=None, enabled=True):
        if source not in self.active_channels(guild):
            raise ValueError("Choose a channel with an enabled AutoDelete rule.")
        if source == destination:
            raise ValueError("The log channel must differ from the source channel.")
        if not types or not set(types) <= TYPES.keys():
            raise ValueError("Choose at least one activity type.")
        old = self.config(guild, source)
        default = DEFAULTS.get(source, ("CHANNEL ACTIVITY LOG", 0x39DCCB))
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO activity_log_config VALUES (?,?,?,?,?,?,?)",
                (
                    guild,
                    source,
                    destination,
                    title or (old["title"] if old else default[0]),
                    color if color is not None else old["color"] if old else default[1],
                    json.dumps(sorted(types)),
                    int(enabled),
                ),
            )

    def cached(self, guild, message):
        row = self.db.execute(
            "SELECT data FROM activity_log_messages WHERE guild=? AND message=?", (guild, message)
        ).fetchone()
        return json.loads(row["data"]) if row else None

    async def named_snapshot(self, m):
        names = {
            u.id: u.display_name for u in getattr(m, "mentions", []) if hasattr(u, "display_name")
        }
        for raw in set(re.findall(r"<@!?(\d+)>", m.content or "")):
            uid = int(raw)
            if uid in names:
                continue
            getter = getattr(m.guild, "get_member", None)
            user = getter(uid) if getter else None
            if user is None:
                try:
                    user = await self.client.fetch_user(uid)
                except (discord.HTTPException, AttributeError):
                    user = None
            if user is not None:
                names[uid] = user.display_name
        return snapshot(m, names)

    def conversation(self, guild, message):
        row = self.db.execute(
            "SELECT context FROM activity_log_links WHERE guild=? AND message=?", (guild, message)
        ).fetchone()
        return json.loads(row["context"]) if row else None

    async def capture_reply(self, m):
        reference = getattr(m, "reference", None)
        target = getattr(reference, "message_id", None)
        if not target or getattr(reference, "channel_id", m.channel.id) != m.channel.id:
            return
        context = self.conversation(m.guild.id, target)
        if context is None:
            original = self.cached(m.guild.id, target)
            if original is None:
                resolved = getattr(reference, "resolved", None)
                if resolved is None or not hasattr(resolved, "author"):
                    try:
                        resolved = await m.channel.fetch_message(target)
                    except (discord.NotFound, discord.Forbidden):
                        resolved = None
                if resolved is not None and hasattr(resolved, "author") and not resolved.author.bot:
                    original = await self.named_snapshot(resolved)
            context = dict(
                root=target,
                user=original["user"] if original else None,
                name=original["name"] if original else "Original author unavailable",
                text=body(original) if original else "Original post is no longer available.",
            )
        if not context.get("user"):
            original = self.cached(m.guild.id, context["root"])
            if original is None:
                try:
                    resolved = await m.channel.fetch_message(context["root"])
                    original = (
                        await self.named_snapshot(resolved) if not resolved.author.bot else None
                    )
                except (discord.NotFound, discord.Forbidden):
                    original = None
            if original:
                context["user"] = original["user"]
                context["name"] = original["name"]
        with self.db:
            self.db.execute(
                "UPDATE activity_log_links SET context=? WHERE guild=? AND root=?",
                (json.dumps(context), m.guild.id, context["root"]),
            )
            for mid in (m.id, context["root"]):
                self.db.execute(
                    "INSERT OR REPLACE INTO activity_log_links VALUES (?,?,?,?)",
                    (m.guild.id, mid, context["root"], json.dumps(context)),
                )

    def queue(self, config, channel, message, action, s, details, token=None):
        stamp = time.time()
        token = (token or uuid.uuid4().hex) + ":" + str(config["destination"])
        details = safe(details).replace(
            "\\-# Images/videos are not retained.", "-# Images/videos are not retained."
        )
        gifs = s.get("gifs", []) if "gifs" in json.loads(config["types"]) else []
        if gifs:
            details += "\n**GIF links (no preview):**\n" + "\n".join(
                "<" + url.replace(">", "%3E").replace("<", "%3C") + ">" for url in gifs
            )
        context = self.conversation(config["guild"], message)
        actor = f"**{safe(s['name'])}** (User ID: `{s['user']}`)"
        event = f"**{action}** · {actor} · <t:{int(stamp)}:T>\n" + details
        member = str(s["user"]) if s["user"] != "unknown" else f"unknown:{message}"
        if context:
            member = "conversation:" + str(context["root"])
        with self.db:
            if self.db.execute(
                "SELECT 1 FROM activity_log_events WHERE token=?", (token,)
            ).fetchone():
                return
            batch = self.db.execute(
                "SELECT * FROM activity_log_batches WHERE guild=? AND source=? AND destination=? AND member=? AND started>? ORDER BY started DESC LIMIT 1",
                (config["guild"], channel, config["destination"], member, stamp - 60),
            ).fetchone()
            if batch is None:
                batch = dict(
                    id="batch:" + uuid.uuid4().hex,
                    guild=config["guild"],
                    source=channel,
                    destination=config["destination"],
                    member=member,
                    started=stamp,
                )
                self.db.execute(
                    "INSERT INTO activity_log_batches VALUES (?,?,?,?,?,?)", tuple(batch.values())
                )
            self.db.execute(
                "INSERT INTO activity_log_events VALUES (?,?,?)", (token, batch["id"], event)
            )
            header = f"**{safe(s['name'])}** (User ID: `{s['user']}`)\nChannel: <#{channel}>\n-# One-minute activity window\n\n"
            if context:
                excerpt = safe(context["text"]).replace(
                    "\\-# Images/videos are not retained.", "-# Images/videos are not retained."
                )
                if len(excerpt) > 700:
                    excerpt = excerpt[:697] + "…"
                header = (
                    f"Channel: <#{channel}>\n-# One-minute conversation window\n\n**Original post — {safe(context['name'])}** (User ID: `{context.get('user') or 'unavailable'}`)\n"
                    + excerpt
                    + "\n\n"
                )
            pages = [""]
            limit = 3900 - len(header)
            for entry in self.db.execute(
                "SELECT body FROM activity_log_events WHERE batch=? ORDER BY rowid", (batch["id"],)
            ):
                remaining = entry["body"]
                if (
                    pages[-1]
                    and len(remaining) <= limit
                    and len(pages[-1]) + 2 + len(remaining) > limit
                ):
                    pages.append("")
                while remaining:
                    room = limit - len(pages[-1]) - (2 if pages[-1] else 0)
                    if room <= 0:
                        pages.append("")
                        continue
                    part, remaining = remaining[:room], remaining[room:]
                    pages[-1] += ("\n\n" if pages[-1] else "") + part
                    if remaining:
                        pages.append("")
            for n, page in enumerate(pages):
                payload = json.dumps(
                    dict(
                        title=config["title"]
                        + (" — CONVERSATION" if context else " — MEMBER ACTIVITY")
                        + (f" · {n + 1}" if n else ""),
                        color=config["color"],
                        text=header + page,
                    )
                )
                self.db.execute(
                    "INSERT INTO activity_log_queue(id,guild,destination,payload,created) VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,state=CASE WHEN activity_log_queue.state='sending' THEN 'sending' ELSE 'pending' END,retry=0 WHERE activity_log_queue.payload!=excluded.payload",
                    (
                        batch["id"] + ":" + str(n),
                        config["guild"],
                        config["destination"],
                        payload,
                        batch["started"],
                    ),
                )

    async def on_message(self, m):
        if not m.guild or m.author.bot or m.webhook_id:
            return
        cfg = self.route(m.guild.id, m.channel.id)
        if not cfg:
            return
        if getattr(m, "stickers", []) and not m.content and not m.attachments and not m.embeds:
            return
        await self.capture_reply(m)
        s = await self.named_snapshot(m)
        old = self.cached(m.guild.id, m.id)
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO activity_log_messages VALUES (?,?,?,?)",
                (m.guild.id, m.channel.id, m.id, json.dumps(s)),
            )
        if old:
            return
        types = set(json.loads(cfg["types"]))
        if (
            ("messages" in types and (s["text"] or not s["kinds"]))
            or ("gifs" in types and "GIF" in s["kinds"])
            or ("media" in types and any(k in s["kinds"] for k in ("image", "video", "attachment")))
        ):
            self.queue(
                cfg, m.channel.id, m.id, "MESSAGE SENT", s, body(s), f"post:{m.guild.id}:{m.id}"
            )

    async def on_edit(self, p):
        if not p.guild_id:
            return
        cfg = self.route(p.guild_id, p.channel_id)
        if not cfg:
            return
        # Embed-only updates are not member edits, but may contain GIF classification.
        if "content" not in p.data and "attachments" not in p.data:
            return
        channel = self.client.get_channel(p.channel_id)
        if not channel:
            return
        try:
            m = await channel.fetch_message(p.message_id)
        except discord.NotFound:
            return
        if m.author.bot or m.webhook_id:
            return
        await self.capture_reply(m)
        previous = self.cached(p.guild_id, p.message_id)
        s = await self.named_snapshot(m)
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO activity_log_messages VALUES (?,?,?,?)",
                (p.guild_id, p.channel_id, p.message_id, json.dumps(s)),
            )
        if previous == s or "edits" not in json.loads(cfg["types"]):
            return
        details = (
            "Before:\n"
            + (body(previous) if previous else "Not captured before logging began.")
            + "\n\nAfter:\n"
            + body(s)
        )
        token = (
            "edit:"
            + str(p.message_id)
            + ":"
            + str(p.data.get("edited_timestamp", uuid.uuid4().hex))
        )
        self.queue(cfg, p.channel_id, p.message_id, "MESSAGE EDITED", s, details, token)

    def five_minute_cleanup(self, guild, channel, message, s):
        if not any(
            r["enabled"] and r["channel"] == channel and r["seconds"] == 300
            for r in self.store.list(guild)
        ):
            return False
        created = s.get("created") if s else None
        if created is None:
            created = discord.utils.snowflake_time(message).timestamp()
        # User-requested timing heuristic; allow the 15-second sweep and dispatch delay.
        return 300 <= time.time() - created <= 330

    async def on_delete(self, p):
        if not p.guild_id:
            return
        cfg = self.route(p.guild_id, p.channel_id)
        if not cfg:
            return
        ids = getattr(p, "message_ids", None) or [p.message_id]
        for mid in ids:
            if self.db.execute(
                "SELECT 1 FROM activity_log_deletions WHERE guild=? AND message=?",
                (p.guild_id, mid),
            ).fetchone():
                with self.db:
                    self.db.execute(
                        "DELETE FROM activity_log_messages WHERE guild=? AND message=?",
                        (p.guild_id, mid),
                    )
                continue
            s = self.cached(p.guild_id, mid)
            if not s:
                m = getattr(p, "cached_message", None)
                if m and not m.author.bot:
                    s = await self.named_snapshot(m)
            if self.five_minute_cleanup(p.guild_id, p.channel_id, mid, s):
                with self.db:
                    self.db.execute(
                        "DELETE FROM activity_log_messages WHERE guild=? AND message=?",
                        (p.guild_id, mid),
                    )
                continue
            if not s:
                s = dict(
                    name="Author unavailable",
                    user="unknown",
                    text="Message was not captured before deletion.",
                    kinds=[],
                    gifs=[],
                )
            if "deletes" in json.loads(cfg["types"]):
                self.queue(
                    cfg,
                    p.channel_id,
                    mid,
                    "MESSAGE DELETED",
                    s,
                    body(s) + "\n\nDeleted by: not identified by Discord.",
                    f"delete:{p.guild_id}:{mid}",
                )
            with self.db:
                self.db.execute(
                    "DELETE FROM activity_log_messages WHERE guild=? AND message=?",
                    (p.guild_id, mid),
                )

    async def on_reaction(self, p, kind):
        if not p.guild_id:
            return
        cfg = self.route(p.guild_id, p.channel_id)
        if kind not in TYPES or not cfg or kind not in json.loads(cfg["types"]):
            return
        uid = getattr(p, "user_id", None)
        user = None
        if uid:
            guild = self.client.get_guild(p.guild_id)
            user = getattr(p, "member", None) or guild.get_member(uid)
            if user is None:
                try:
                    user = await self.client.fetch_user(uid)
                except discord.NotFound:
                    pass
            if user and user.bot:
                return
        name = user.display_name if user else "Actor unavailable"
        emoji = getattr(p, "emoji", None)
        label = (
            (
                f"<{'a' if getattr(emoji, 'animated', False) else ''}:{emoji.name}:{emoji.id}>"
                if emoji.id
                else str(emoji)
            )
            if emoji
            else "all reactions"
        )
        action = {"reaction_remove": "REACTION REMOVED", "reaction_clear": "REACTIONS CLEARED"}[
            kind
        ]
        self.queue(
            cfg,
            p.channel_id,
            p.message_id,
            action,
            dict(name=name, user=uid or "unknown", gifs=[]),
            "Reaction: " + label,
        )

    def begin_deletion(self, m):
        if not getattr(m, "guild", None) or not self.route(m.guild.id, m.channel.id):
            return
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO activity_log_deletions VALUES (?,?,'pending')",
                (m.guild.id, m.id),
            )
            if not m.author.bot and not getattr(m, "webhook_id", None):
                self.db.execute(
                    "INSERT OR REPLACE INTO activity_log_messages VALUES (?,?,?,?)",
                    (m.guild.id, m.channel.id, m.id, json.dumps(snapshot(m))),
                )

    def finish_deletion(self, m, success):
        if not getattr(m, "guild", None):
            return
        with self.db:
            if not success:
                self.db.execute(
                    "DELETE FROM activity_log_deletions WHERE guild=? AND message=?",
                    (m.guild.id, m.id),
                )
                return
            self.db.execute(
                "UPDATE activity_log_deletions SET status='confirmed' WHERE guild=? AND message=?",
                (m.guild.id, m.id),
            )

    def start(self):
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self.run(), name="ky-autodelete-logs")

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    async def run(self):
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            try:
                for guild in self.client.guilds:
                    self.seed(guild)
                await self.expire_logs()
                await self.flush()
            except Exception:
                logging.exception("AutoDelete activity logs will retry")
            await asyncio.sleep(3)

    def track_retention(self, guild, destination, message):
        # Discord IDs encode the actual posting time, including pre-upgrade logs.
        expires = discord.utils.snowflake_time(message).timestamp() + RETENTION_SECONDS
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO activity_log_retention(message,guild,destination,expires) VALUES (?,?,?,?)",
                (message, guild, destination, expires),
            )

    async def expire_logs(self):
        now = time.time()
        rows = self.db.execute(
            "SELECT * FROM activity_log_retention WHERE expires<=? AND retry<=? ORDER BY expires LIMIT 25",
            (now, now),
        ).fetchall()
        for row in rows:
            try:
                channel = self.client.get_channel(
                    row["destination"]
                ) or await self.client.fetch_channel(row["destination"])
                if channel.guild.id != row["guild"]:
                    raise ValueError("Wrong retention guild")
                message = await channel.fetch_message(row["message"])
                if message.author.id != self.client.user.id:
                    raise ValueError("Retention only deletes KY BOT log messages")
                # Discord cannot bulk-delete messages older than 14 days.
                await message.delete()
            except discord.NotFound:
                pass
            except Exception:
                with self.db:
                    self.db.execute(
                        "UPDATE activity_log_retention SET retry=? WHERE message=?",
                        (now + 3600, row["message"]),
                    )
                logging.exception(
                    "AutoDelete log retention will retry for message %s", row["message"]
                )
                continue
            with self.db:
                self.db.execute(
                    "DELETE FROM activity_log_reactions WHERE key IN (SELECT id FROM activity_log_queue WHERE message=?)",
                    (row["message"],),
                )
                self.db.execute("DELETE FROM activity_log_queue WHERE message=?", (row["message"],))
                self.db.execute(
                    "DELETE FROM activity_log_retention WHERE message=?", (row["message"],)
                )
                empty = [
                    r["id"]
                    for r in self.db.execute(
                        "SELECT id FROM activity_log_batches b WHERE NOT EXISTS (SELECT 1 FROM activity_log_queue q WHERE q.id LIKE b.id || ':%')"
                    )
                ]
                for batch in empty:
                    self.db.execute("DELETE FROM activity_log_events WHERE batch=?", (batch,))
                    self.db.execute("DELETE FROM activity_log_batches WHERE id=?", (batch,))

    async def flush(self):
        rows = self.db.execute(
            "SELECT * FROM activity_log_queue WHERE state IN ('pending','sending') AND retry<=? ORDER BY created LIMIT 20",
            (time.time(),),
        ).fetchall()
        for raw in rows:
            row = dict(raw)
            try:
                channel = self.client.get_channel(
                    row["destination"]
                ) or await self.client.fetch_channel(row["destination"])
                if channel.guild.id != row["guild"]:
                    raise ValueError("Wrong log guild")
                data = json.loads(row["payload"])
                embed = discord.Embed(
                    title=data["title"],
                    description=data["text"],
                    color=data["color"],
                    timestamp=dt.datetime.fromtimestamp(row["created"], dt.UTC),
                )
                message = None
                if row["message"]:
                    try:
                        message = await channel.fetch_message(row["message"])
                    except discord.NotFound:
                        pass
                if message is None and row["state"] == "sending":
                    if time.time() - row["sending"] < 120:
                        continue
                    async for m in channel.history(
                        limit=None,
                        after=dt.datetime.fromtimestamp(row["sending"] - 5, dt.UTC),
                    ):
                        if m.author.id == self.client.user.id and any(
                            e.footer.text == "KY BOT activity record · " + row["id"]
                            or (e.timestamp == embed.timestamp and e.title == embed.title)
                            for e in m.embeds
                        ):
                            message = m
                            break
                with self.db:
                    changed = self.db.execute(
                        "UPDATE activity_log_queue SET state='sending',sending=? WHERE id=? AND state=?",
                        (time.time(), row["id"], row["state"]),
                    ).rowcount
                if not changed:
                    continue
                if message is None:
                    message = await channel.send(
                        embed=embed, allowed_mentions=discord.AllowedMentions.none()
                    )
                else:
                    await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                self.track_retention(row["guild"], row["destination"], message.id)
                with self.db:
                    self.db.execute(
                        "UPDATE activity_log_queue SET state=CASE WHEN payload=? THEN 'sent' ELSE 'pending' END,message=? WHERE id=?",
                        (row["payload"], message.id, row["id"]),
                    )
            except discord.Forbidden:
                with self.db:
                    self.db.execute(
                        "UPDATE activity_log_queue SET state='pending',retry=? WHERE id=?",
                        (time.time() + 60, row["id"]),
                    )
                logging.warning("AutoDelete log channel %s denied access", row["destination"])
            except Exception:
                with self.db:
                    self.db.execute(
                        "UPDATE activity_log_queue SET retry=? WHERE id=?",
                        (time.time() + 60, row["id"]),
                    )
                logging.exception("AutoDelete activity record delivery will retry")
