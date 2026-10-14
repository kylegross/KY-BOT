"""Shared cleanup matching and restart-safe history sweeps. No bot login on import."""

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from urllib.parse import urlparse

import discord

BACKFILL_LIMIT = 10
SWEEP_INTERVAL = 15


def media_types(message):
    kinds = set()
    for attachment in message.attachments:
        name = attachment.filename.lower()
        mime = (attachment.content_type or "").lower()
        if name.endswith(".gif") or mime == "image/gif":
            kinds.add("gif")
        elif mime.startswith("image/") or name.endswith(
            (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".avif")
        ):
            kinds.add("image")
        elif mime.startswith("video/") or name.endswith((".mp4", ".webm", ".mov", ".mkv")):
            kinds.add("video")
    for embed in message.embeds:
        host = (urlparse(embed.url or "").hostname or "").lower()
        gif = (
            host in {"tenor.com", "www.tenor.com", "giphy.com", "www.giphy.com", "media.giphy.com"}
            or embed.type == "gifv"
        )
        if gif:
            kinds.add("gif")
        else:
            if embed.type == "image" or embed.image:
                kinds.add("image")
            if embed.type == "video" or embed.video:
                kinds.add("video")
    return kinds


def matches(message, content, thread=False, kinds=None):
    keys = set(content)
    kinds = media_types(message) if kinds is None else kinds
    # Explicit bot media policy applies to posts, before broad message filters.
    # Keep wins for mixed image/video posts because Discord deletes whole messages.
    if not thread and getattr(getattr(message, "author", None), "bot", False):
        present = ({"image"} if kinds & {"image", "gif"} else set()) | (
            {"video"} if "video" in kinds else set()
        )
        if any("botmedia_keep_" + kind in keys for kind in present):
            return False
        if any("botmedia_delete_" + kind in keys for kind in present):
            return True
    # A single author choice restricts the whole rule, including media and threads.
    # Both author choices (or All messages) explicitly include everyone.
    authors = keys & {"messages_user", "messages_bot"}
    if "messages_all" not in keys and len(authors) == 1:
        author = getattr(message, "author", None)
        if author is None:
            return False
        if bool(author.bot) != ("messages_bot" in authors):
            return False
    kinds = media_types(message) if kinds is None else kinds
    if thread:
        return (
            "threads_all" in keys
            or ("threads_text" in keys and not kinds)
            or any("threads_" + kind in keys for kind in kinds)
            or ("threads_image" in keys and "gif" in kinds)
        )
    return (
        "messages_all" in keys
        or ("messages_bot" in keys and message.author.bot)
        or ("messages_user" in keys and not message.author.bot)
        or ("media_all" in keys and bool(kinds))
        or any("media_" + kind in keys for kind in kinds)
        or ("media_image" in keys and "gif" in kinds)
    )


def check_permissions(channel, content, member=None):
    if member is not None and not channel.permissions_for(member).view_channel:
        raise ValueError("You can no longer view this channel.")
    permissions = channel.permissions_for(channel.guild.me)
    if not permissions.view_channel or not permissions.read_message_history:
        raise ValueError("KY BOT needs View Channel and Read Message History.")
    if (
        any(not key.startswith(("threads_", "botmedia_keep_")) for key in content)
        and not permissions.manage_messages
    ):
        raise ValueError("KY BOT needs Manage Messages.")
    if any(key.startswith("threads_") for key in content) and not permissions.manage_threads:
        raise ValueError("KY BOT needs Manage Threads.")


async def delete_matching(message, rules, store=None, thread=None, thread_kinds=None, logs=None):
    now = discord.utils.utcnow()
    created = thread.created_at if thread is not None else message.created_at
    for rule in rules:
        if store is not None and not store.enabled(rule["id"], rule):
            continue
        if created is not None and created.timestamp() < rule.get("starts_at", 0):
            continue
        if created is None:
            continue
        age = (rule.get("as_of", now) - created).total_seconds()
        if rule.get("age_mode") == "within":
            if not 0 <= age <= rule["seconds"]:
                continue
        elif age < rule["seconds"]:
            continue
        if matches(message, rule["content"], thread=thread is not None, kinds=thread_kinds):
            try:
                if thread is not None:
                    await thread.delete(reason="KY BOT AutoDelete")
                else:
                    # Message.delete accepts only delay; audit-log reasons are thread-only here.
                    logs = logs or (
                        getattr(store, "activity_logs", None) if store is not None else None
                    )
                    if logs:
                        logs.begin_deletion(message)
                    try:
                        await message.delete()
                    except BaseException:
                        if logs:
                            logs.finish_deletion(message, False)
                        raise
                    if logs:
                        logs.finish_deletion(message, True)
            except discord.NotFound:
                return 0
            return 1
    return 0


class AutoDeleteService:
    def __init__(self, client, store):
        self.client = client
        self.store = store
        self.cursors = {}
        self.locks = defaultdict(asyncio.Lock)
        self.task = None
        self.channel_tasks = {}
        self.archive_cursors = {}
        from .autodelete_logging import ActivityLogs

        self.logs = ActivityLogs(client, store)

    def due(self, message, rules):
        age = (discord.utils.utcnow() - message.created_at).total_seconds()
        return any(
            self.store.enabled(r["id"], r)
            and message.created_at.timestamp() >= r["starts_at"]
            and age >= r["seconds"]
            and matches(message, r["content"])
            for r in rules
        )

    async def delete_batch(self, channel, messages, rules):
        # Only recent messages may use Discord's 2–100-message bulk endpoint.
        # Recheck rule state after every network await so TURN OFF stops queued work.
        remaining = list({m.id: m for m in messages}.values())
        deleted = 0
        while remaining:
            batch = remaining[:100]
            remaining = remaining[100:]
            batch = [m for m in batch if self.due(m, rules)]
            cutoff = discord.utils.utcnow() - timedelta(days=14) + timedelta(minutes=1)
            recent = [m for m in batch if m.created_at > cutoff]
            older = [m for m in batch if m.created_at <= cutoff]
            if len(recent) > 1:
                check_permissions(
                    channel,
                    {
                        k
                        for r in rules
                        if self.store.enabled(r["id"], r)
                        for k in r["content"]
                        if not k.startswith("threads_")
                    },
                )
                for message in recent:
                    self.logs.begin_deletion(message)
                try:
                    await channel.delete_messages(recent, reason="KY BOT AutoDelete")
                    for message in recent:
                        self.logs.finish_deletion(message, True)
                    deleted += len(recent)
                except discord.HTTPException as error:
                    for message in recent:
                        self.logs.finish_deletion(message, False)
                    if error.code != 50034:
                        raise
                    # A message can cross the age boundary while waiting on a rate limit.
                    older = recent + older
            else:
                older = recent + older
            for message in older:
                deleted += await delete_matching(message, rules, self.store)
        return deleted

    async def sweep_recent(self, channel, rules):
        """Start at each rule's due-time boundary, not the beginning of channel history."""
        active = [
            r
            for r in rules
            if self.store.enabled(r["id"], r)
            and any(not k.startswith("threads_") for k in r["content"])
        ]
        if not active:
            return
        check_permissions(
            channel, {k for r in active for k in r["content"] if not k.startswith("threads_")}
        )
        async with self.locks[channel.id]:
            seen = set()
            for seconds in sorted({r["seconds"] for r in active}):
                boundary = discord.utils.utcnow() - timedelta(seconds=seconds)
                starts_at = datetime.fromtimestamp(
                    min(r["starts_at"] for r in active if r["seconds"] == seconds), UTC
                )
                if boundary <= starts_at:
                    continue
                limit = BACKFILL_LIMIT if seconds >= 13 * 86400 else 100
                pending = []
                async for message in channel.history(
                    limit=limit, before=boundary, after=starts_at, oldest_first=False
                ):
                    if message.id in seen:
                        continue
                    seen.add(message.id)
                    if message.flags.has_thread:
                        self.store.remember_thread(
                            channel.guild.id, message.id, media_types(message)
                        )
                    if self.due(message, active):
                        pending.append(message)
                # A recent due boundary may still return an entirely old channel history.
                old_cutoff = discord.utils.utcnow() - timedelta(days=14) + timedelta(minutes=1)
                recent = [m for m in pending if m.created_at > old_cutoff]
                old = [m for m in pending if m.created_at <= old_cutoff][:BACKFILL_LIMIT]
                await self.delete_batch(channel, recent + old, active)

    def start(self):
        self.logs.start()
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run(), name="ky-autodelete")

    async def close(self):
        await self.logs.close()
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        for task in self.channel_tasks.values():
            task.cancel()
        await asyncio.gather(*self.channel_tasks.values(), return_exceptions=True)
        self.channel_tasks.clear()

    async def cleanup(self, channel, content, seconds, limit, member, age_mode="older"):
        if age_mode not in {"within", "older"}:
            raise ValueError("Select a valid cleanup time window.")
        if limit == 0 and age_mode != "within":
            raise ValueError("All messages requires Within the past timeframe.")
        check_permissions(channel, content, member)
        rule = {"content": content, "seconds": seconds, "age_mode": age_mode}
        scanned = deleted = threads = 0
        # Freeze the scan bounds: arrivals during cleanup belong to future rules, not this job.
        now = discord.utils.utcnow()
        rule["as_of"] = now
        history_options = {"limit": limit or None}
        if age_mode == "within":
            history_options.update(
                after=now - timedelta(seconds=seconds), before=now, oldest_first=False
            )
        async for message in channel.history(**history_options):
            async with self.locks[channel.id]:
                current = channel.guild.get_member(member.id)
                if current is None or not (
                    current.guild_permissions.administrator
                    or current.guild_permissions.manage_guild
                ):
                    raise ValueError("Your administrative role is no longer available.")
                check_permissions(channel, content, current)
                scanned += 1
                if message.flags.has_thread:
                    self.store.remember_thread(channel.guild.id, message.id, media_types(message))
                if any(k.startswith("threads_") for k in content) and message.flags.has_thread:
                    thread = channel.guild.get_thread(message.id)
                    if thread is None:
                        try:
                            thread = await self.client.fetch_channel(message.id)
                        except discord.NotFound:
                            thread = None
                    if isinstance(thread, discord.Thread):
                        threads += await delete_matching(message, [rule], thread=thread)
                deleted += await delete_matching(message, [rule], logs=self.logs)
        return scanned, deleted, threads

    async def sweep(self, channel, rules):
        # Catch up only within the rules' saved start boundaries, never older channel history.
        content = {key for rule in rules for key in rule["content"]}
        check_permissions(channel, content)
        async with self.locks[channel.id]:
            if any(k.startswith("threads_") for k in content):
                threads = {t.id: t for t in channel.threads}
                archived = []
                async for thread in channel.archived_threads(
                    limit=25, before=self.archive_cursors.get(channel.id)
                ):
                    archived.append(thread)
                    threads[thread.id] = thread
                if len(archived) == 25:
                    self.archive_cursors[channel.id] = archived[-1].archive_timestamp
                else:
                    self.archive_cursors.pop(channel.id, None)
                for thread in threads.values():
                    if not any(self.store.enabled(r["id"], r) for r in rules):
                        break
                    if thread.created_at is None or thread.created_at.timestamp() < min(
                        r["starts_at"] for r in rules
                    ):
                        continue
                    try:
                        starter = await channel.fetch_message(thread.id)
                        kinds = media_types(starter)
                        self.store.remember_thread(channel.guild.id, thread.id, kinds)
                    except discord.NotFound:
                        starter = None
                        kinds = self.store.thread_kinds(channel.guild.id, thread.id)
                        # Unknown is distinct from a known plain-text starter.
                        if kinds is None:
                            kinds = {"unknown"}
                    if await delete_matching(starter, rules, self.store, thread, kinds):
                        self.store.forget_thread(channel.guild.id, thread.id)
            starts_at = datetime.fromtimestamp(min(r["starts_at"] for r in rules), UTC)
            start_id = discord.utils.time_snowflake(starts_at)
            cursor = max(self.cursors.get(channel.id, 0), start_id)
            seen = 0
            async for message in channel.history(
                limit=BACKFILL_LIMIT, oldest_first=True, after=discord.Object(cursor)
            ):
                seen += 1
                if message.flags.has_thread:
                    self.store.remember_thread(channel.guild.id, message.id, media_types(message))
                await delete_matching(message, rules, self.store)
                self.cursors[channel.id] = message.id
            if seen < BACKFILL_LIMIT:
                self.cursors.pop(channel.id, None)

    async def run_channel(self, channel):
        while not self.client.is_closed():
            rules = [
                r
                for r in self.store.list(channel.guild.id)
                if r["channel"] == channel.id and r["enabled"]
            ]
            if not rules:
                return
            try:
                await self.sweep_recent(channel, rules)
            except Exception:
                logging.exception("AutoDelete recent sweep failed in channel %s", channel.id)
            try:
                await self.sweep(channel, rules)
            except Exception:
                logging.exception("AutoDelete history sweep failed in channel %s", channel.id)
            await asyncio.sleep(SWEEP_INTERVAL)

    async def run(self):
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            try:
                for guild in self.client.guilds:
                    grouped = defaultdict(list)
                    for rule in self.store.list(guild.id):
                        if rule["enabled"]:
                            grouped[rule["channel"]].append(rule)
                    for channel_id, rules in grouped.items():
                        channel = guild.get_channel(channel_id)
                        if not isinstance(channel, discord.TextChannel):
                            continue
                        task = self.channel_tasks.get(channel_id)
                        if task is None or task.done():
                            self.channel_tasks[channel_id] = asyncio.create_task(
                                self.run_channel(channel), name=f"ky-autodelete-{channel_id}"
                            )
            except Exception:
                logging.exception("AutoDelete sweep failed; retrying next cycle")
            await asyncio.sleep(SWEEP_INTERVAL)
