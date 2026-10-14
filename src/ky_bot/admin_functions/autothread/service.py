"""Public threads from new posts; durable deduplication and warning cleanup."""

import asyncio
import logging
import re
import time
from pathlib import PurePosixPath
from urllib.parse import unquote, urlparse

import discord
import regex

from .store import STARTER, category_color

PINK = 0xE989BA
WARNING = (
    "I SAW THAT! Please ONLY post photos and/or videos within this "
    "channel. To comment on someone's post, please chat within the "
    "respective thread. Thank you!"
)


def starter_embed(rule, author, channel, message_id=None):
    text = rule.get("starter_message", STARTER).replace("{user}", f"<@{author.id}>")
    name = discord.utils.escape_mentions(discord.utils.escape_markdown(author.display_name))
    text = text.replace("{display_name}", name)
    embed = discord.Embed(
        description=text,
        color=rule.get("embed_color")
        if rule.get("embed_color") is not None
        else category_color(channel),
    )
    return embed


def attachment_types(message):
    if getattr(getattr(message, "flags", None), "voice", False):
        return {"voice"}
    kinds = set()
    for a in message.attachments:
        if getattr(a, "duration", None) is not None and getattr(a, "waveform", None) is not None:
            kinds.add("voice")
            continue
        mime = (a.content_type or "").lower().split(";")[0]
        ext = PurePosixPath(a.filename.lower()).suffix
        if mime == "image/gif" or ext == ".gif":
            kinds.add("gif")
        elif mime.startswith("image/") or (
            not mime
            and ext in {".png", ".jpg", ".jpeg", ".webp", ".avif", ".heic", ".heif", ".bmp"}
        ):
            kinds.add("image")
        elif mime.startswith("video/") or (
            not mime and ext in {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
        ):
            kinds.add("video")
    return kinds


def has_media_link(message):
    # Inspect URLs and Discord's preview metadata only; never fetch user URLs.
    for url in re.findall(r"https?://[^\s<>]+", message.content or "", re.IGNORECASE):
        try:
            parsed = urlparse(url.rstrip(".,!?)]}"))
        except ValueError:
            continue
        host = (parsed.hostname or "").lower()
        path = unquote(parsed.path).lower()
        if PurePosixPath(path).suffix in {
            ".gif",
            ".gifv",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".avif",
            ".mp4",
            ".webm",
            ".mov",
            ".m4v",
        }:
            return True
        if any(
            host == domain or host.endswith("." + domain) for domain in ("tenor.com", "giphy.com")
        ) and path not in ("", "/"):
            return True
    for embed in getattr(message, "embeds", ()):
        if embed.type in ("image", "video", "gifv") or embed.image or embed.video:
            return True
    return False


def qualifies(message, rule):
    kinds = attachment_types(message)
    if rule["require_attachment"] and not kinds & {"image", "video"}:
        return False
    if rule["media_only"] and (kinds & {"gif", "voice"} or not kinds & set(rule["content"])):
        return False
    return bool(
        kinds & set(rule["content"])
        or ("links" in rule["content"] and has_media_link(message))
        or ("text" in rule["content"] and message.content and not kinds)
    )


def thread_name(display_name):
    # Truncate at Unicode grapheme boundaries: never split emoji/combining sequences.
    suffix = "'s thread"
    name = ""
    for cluster in regex.findall(r"\X", display_name):
        if len(name) + len(cluster) + len(suffix) > 100:
            break
        name += cluster
    return (name or "User") + suffix


def check_permissions(channel, media_only=False):
    p = channel.permissions_for(channel.guild.me)
    required = [
        "view_channel",
        "read_message_history",
        "create_public_threads",
        "send_messages_in_threads",
        "send_messages",
        "embed_links",
    ]
    if media_only:
        required += ["manage_messages"]
    missing = [name.replace("_", " ").title() for name in required if not getattr(p, name, False)]
    if missing:
        raise ValueError("KY BOT needs: " + ", ".join(missing) + ".")


class AutothreadService:
    def __init__(self, client, store):
        self.client = client
        self.store = store
        self.task = None
        # Fixed stripes serialize repeated deliveries without an unbounded lock cache.
        self.locks = [asyncio.Lock() for _ in range(64)]
        self.archive_next_check = 0
        self.archive_retry = {}

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.worker())

    async def close(self):
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def on_raw_message_edit(self, payload):
        if not payload.guild_id or not {"content", "embeds"} & payload.data.keys():
            return
        if not any(
            r["channel"] == payload.channel_id and r["enabled"] and "links" in r["content"]
            for r in self.store.list(payload.guild_id)
        ):
            return
        job = self.store.job(payload.message_id)
        if job and job["done"]:
            return
        try:
            channel = self.client.get_channel(
                payload.channel_id
            ) or await self.client.fetch_channel(payload.channel_id)
            if not isinstance(channel, discord.TextChannel):
                return
            message = await channel.fetch_message(payload.message_id)
            await self.on_message(message, links_only=True)
        except discord.NotFound:
            pass

    async def on_message(self, message, *, links_only=False):
        if message.guild is None or not isinstance(message.channel, discord.TextChannel):
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        webhook = message.webhook_id is not None
        if message.author.bot and not webhook:
            return
        async with self.locks[message.id % 64]:
            job = self.store.job(message.id)
            if job:
                if job["done"] or job["attempts"] >= 5:
                    return
                rule = self.store.rule(job["rule_id"])
                if not rule or not rule["enabled"]:
                    self.store.finish(message.id)
                    return
                await self.deliver(message, rule)
                return
            rules = [
                r
                for r in self.store.list(message.guild.id)
                if r["channel"] == message.channel.id
                and r["enabled"]
                and message.created_at.timestamp() >= r["starts_at"]
                and not (webhook and r["ignore_webhooks"])
            ]
            if not rules:
                return
            if links_only and not any("links" in r["content"] for r in rules):
                return
            kinds = attachment_types(message)
            enforcing = [r for r in rules if r["media_only"]]
            allowed = set().union(*(set(r["content"]) for r in enforcing)) if enforcing else set()
            if enforcing and (kinds & {"gif", "voice"} or not kinds & allowed):
                check_permissions(message.channel, True)
                try:
                    await message.delete()
                except discord.NotFound:
                    return
                self.store.claim(message, enforcing[0])
                self.store.finish(message.id)
                notice = await message.channel.send(
                    embed=discord.Embed(description=WARNING, color=PINK),
                    delete_after=10,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                self.store.notice(notice)
                return
            rule = next(
                (
                    r
                    for r in rules
                    if (not links_only or "links" in r["content"]) and qualifies(message, r)
                ),
                None,
            )
            if rule is None:
                return
            check_permissions(message.channel, bool(enforcing))
            self.store.claim(message, rule)
            await self.deliver(message, rule)

    async def deliver(self, message, rule):
        try:
            # A thread created from a message has that message's ID. Discord also
            # enforces this uniqueness, including across processes and restarts.
            thread = message.guild.get_thread(message.id)
            if thread is None:
                try:
                    thread = await self.client.fetch_channel(message.id)
                except discord.NotFound:
                    thread = await message.create_thread(
                        name=thread_name(message.author.display_name),
                        auto_archive_duration=rule["archive"],
                        slowmode_delay=0,
                        reason="KY BOT Autothread",
                    )
            if not isinstance(thread, discord.Thread):
                raise ValueError("Unexpected channel for starter message.")
            if thread.owner_id != self.client.user.id:
                self.store.finish(message.id)
                return
            member = message.guild.get_member(message.author.id)
            if member is None and message.webhook_id is None:
                member = await message.guild.fetch_member(message.author.id)
            if member is not None:
                await thread.add_user(member)
            embed = starter_embed(rule, message.author, message.channel, message.id)
            saved = self.store.welcome(message.id, embed.description, embed.colour.value)
            embed = discord.Embed(description=saved["description"], color=saved["color"])
            text = STARTER.format(user=f"<@{message.author.id}>")
            # Recovery after an ambiguous send or restart: check earliest posts
            # before sending again. The starter belongs at the beginning.
            found = False
            async for old in thread.history(limit=None, oldest_first=True):
                if old.author.id == self.client.user.id and (
                    old.content == text
                    or any(
                        e.footer.text == f"KY BOT • Autothread {message.id}"
                        or (e.description == embed.description and e.colour == embed.colour)
                        for e in getattr(old, "embeds", ())
                    )
                ):
                    found = True
                    break
            if not found:
                await thread.send(
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False,
                        roles=False,
                        users=[discord.Object(id=message.author.id)],
                        replied_user=False,
                    ),
                )
            self.store.finish(message.id)
        except discord.NotFound:
            self.store.finish(message.id)
        except (discord.HTTPException, OSError, ValueError):
            self.store.retry(message.id)
            logging.exception(
                "Autothread delivery failed for message %s; bounded retry scheduled", message.id
            )

    async def tick(self):
        for row in self.store.expired_notices():
            try:
                channel = self.client.get_channel(
                    row["channel"]
                ) or await self.client.fetch_channel(row["channel"])
                await channel.get_partial_message(row["message"]).delete()
            except discord.NotFound:
                pass
            except discord.HTTPException:
                logging.exception("Could not remove Autothread warning %s", row["message"])
                continue
            self.store.forget_notice(row["message"])
        for row in self.store.pending():
            try:
                channel = self.client.get_channel(
                    row["channel"]
                ) or await self.client.fetch_channel(row["channel"])
                message = await channel.fetch_message(row["message"])
                await self.on_message(message)
            except discord.NotFound:
                self.store.finish(row["message"])
            except (discord.HTTPException, ValueError):
                self.store.retry(row["message"])
                logging.exception("Autothread retry failed")
        await self.archive_inactive()

    async def archive_inactive(self):
        """Explicitly archive only KY BOT-created, tracked threads after inactivity.

        Discord's auto_archive_duration now controls sidebar visibility. Read
        active threads from Discord so restart/cache misses do not lose timers.
        """
        now = time.time()
        if now < self.archive_next_check:
            return
        self.archive_next_check = now + 60
        self.archive_retry = {
            tid: until for tid, until in self.archive_retry.items() if until > now
        }
        for guild in getattr(self.client, "guilds", ()):
            if not any(r["enabled"] for r in self.store.list(guild.id)):
                continue
            try:
                threads = await guild.active_threads()
            except discord.HTTPException:
                logging.exception("Could not check AutoThread archive timers in guild %s", guild.id)
                continue
            rules = self.store.archive_rules(guild.id, [t.id for t in threads])
            for thread in threads:
                rule = rules.get(thread.id)
                if (
                    not rule
                    or thread.archived
                    or thread.owner_id != self.client.user.id
                    or thread.parent_id != rule["channel"]
                    or thread.id in self.archive_retry
                ):
                    continue
                # Unarchiving and changing Discord's duration also count as activity.
                timestamps = [
                    d.timestamp()
                    for d in (thread.created_at, thread.archive_timestamp)
                    if d is not None
                ]
                if thread.last_message_id:
                    timestamps.append(
                        discord.utils.snowflake_time(thread.last_message_id).timestamp()
                    )
                if not timestamps:
                    continue
                cutoff = now - rule["archive"] * 60
                if max(timestamps) > cutoff:
                    continue
                try:
                    # Confirm the newest message before archiving; count all authors.
                    latest = None
                    async for message in thread.history(limit=1):
                        latest = message.created_at.timestamp()
                    if latest is not None and latest > cutoff:
                        continue
                    # Rules can be edited/disabled while awaiting Discord.
                    current = self.store.archive_rules(guild.id, [thread.id]).get(thread.id)
                    if current != rule:
                        continue
                    await thread.edit(
                        archived=True,
                        reason="KY BOT AutoThread: configured inactivity period elapsed",
                    )
                except discord.NotFound:
                    continue
                except (discord.HTTPException, OSError):
                    self.archive_retry[thread.id] = time.time() + 300
                    logging.exception(
                        (
                            "Could not archive AutoThread %s; check thread access and Manage "
                            "Threads permission"
                        ),
                        thread.id,
                    )

    async def worker(self):
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            try:
                await self.tick()
            except Exception:
                logging.exception("Autothread maintenance failed")
            await asyncio.sleep(10)
