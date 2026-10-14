import asyncio
import logging
import re
import time

import discord
import regex

from ky_bot.admin_functions.autothread.service import attachment_types, has_media_link

from .store import Store


def emoji_key(value):
    match = re.fullmatch(r"<a?:\w+:(\d+)>", value)
    return match[1] if match else value.replace("\ufe0f", "")


def parse_emojis(text, guild):
    result = []
    position = 0
    while position < len(text):
        if text[position].isspace() or text[position] == ",":
            position += 1
            continue
        custom = re.match(r"<a?:\w+:(\d+)>|:([A-Za-z0-9_]+):", text[position:])
        if custom:
            if custom[1]:
                emoji = guild.get_emoji(int(custom[1]))
            else:
                found = [e for e in guild.emojis if e.name == custom[2]]
                if len(found) != 1:
                    raise ValueError(
                        "Use an exact server emoji name, or paste its <:name:id> form."
                    )
                emoji = found[0]
            if emoji is None or not emoji.is_usable():
                raise ValueError("That server emoji is missing or unavailable to KY BOT.")
            value = str(emoji)
            position += len(custom[0])
        else:
            value = regex.match(r"\X", text[position:])[0]
            position += len(value)
            if not (
                regex.fullmatch(r"\p{Regional_Indicator}{2}|[#*0-9]\ufe0f?\u20e3", value)
                or (
                    regex.search(r"\p{Extended_Pictographic}", value)
                    and not regex.search(r"[\p{L}\p{N}]", value)
                )
            ):
                raise ValueError(
                    "Paste standard emojis or server emojis such as :emoji_name:. "
                    "Text is not a reaction."
                )
        if emoji_key(value) not in {emoji_key(e) for e in result}:
            result.append(value)
    if not result:
        raise ValueError("Enter at least one emoji.")
    if len(result) > 20:
        raise ValueError("Choose no more than 20 emojis.")
    return result


def resolve_emoji(value, guild):
    parsed = re.fullmatch(r"<a?:(\w+):(\d+)>", value)
    if parsed:
        emoji = guild.get_emoji(int(parsed[2]))
        if emoji is None or not emoji.is_usable():
            raise ValueError(f"Server emoji {parsed[1]} is no longer available to KY BOT.")
        return emoji
    return value


def permissions(channel):
    p = channel.permissions_for(channel.guild.me)
    missing = [
        name
        for name in ("view_channel", "read_message_history", "add_reactions")
        if not getattr(p, name, False)
    ]
    if missing:
        raise ValueError(
            "KY BOT needs "
            + ", ".join(n.replace("_", " ") for n in missing)
            + f" in #{channel.name}."
        )


def matches(message, settings):
    if message.webhook_id is not None:
        if not settings["webhooks"]:
            return False
    elif message.author.bot and not settings["bots"]:
        return False
    if isinstance(message.channel, discord.Thread) and not settings["threads"]:
        return False
    content = set(settings["content"])
    if "all" in content:
        return True
    kinds = attachment_types(message)
    if has_media_link(message):
        kinds.add("links")
    if message.content and not kinds:
        kinds.add("text")
    return bool(kinds & content)


class AutoReactService:
    def __init__(self, client, store=None):
        self.client = client
        self.store = store or Store()
        self.task = None

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.worker())

    async def close(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.store.db.close()

    async def on_message(self, message):
        if not message.guild or not isinstance(
            message.channel, (discord.TextChannel, discord.Thread)
        ):
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        parent = (
            message.channel.parent_id
            if isinstance(message.channel, discord.Thread)
            else message.channel.id
        )
        rule = self.store.get(message.guild.id, parent)
        if not rule or not rule["enabled"] or message.created_at.timestamp() < rule["starts"]:
            return
        if matches(message, rule["settings"]):
            self.store.enqueue(message, rule)

    async def on_raw_message_edit(self, payload):
        if not payload.guild_id or not {"embeds", "content"} & payload.data.keys():
            return
        channel = self.client.get_channel(payload.channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            return
        cid = channel.parent_id if isinstance(channel, discord.Thread) else channel.id
        rule = self.store.get(payload.guild_id, cid)
        if not rule or not rule["enabled"] or "links" not in rule["settings"]["content"]:
            return
        try:
            message = await channel.fetch_message(payload.message_id)
            if time.time() - message.created_at.timestamp() <= 300:
                await self.on_message(message)
        except discord.NotFound:
            pass

    async def deliver(self, job):
        rule = self.store.get(job["guild"], job["parent"])
        if not rule or not rule["enabled"] or rule["revision"] != job["revision"]:
            self.store.update_job(job["message"], done=1)
            return
        try:
            channel = self.client.get_channel(job["channel"]) or await self.client.fetch_channel(
                job["channel"]
            )
            if channel.guild.id != job["guild"]:
                raise ValueError("Reaction channel belongs to another server.")
            permissions(channel)
            message = await channel.fetch_message(job["message"])
            if not matches(message, rule["settings"]):
                self.store.update_job(job["message"], done=1)
                return
            had_error = False
            for index in range(job["position"], len(job["emojis"])):
                current = self.store.get(job["guild"], job["parent"])
                if not current or not current["enabled"] or current["revision"] != job["revision"]:
                    self.store.update_job(job["message"], done=1)
                    return
                try:
                    await message.add_reaction(resolve_emoji(job["emojis"][index], channel.guild))
                except ValueError as exc:
                    self.store.error(rule, str(exc))
                    had_error = True
                except discord.NotFound as exc:
                    if exc.code != 10014:
                        raise
                    self.store.error(
                        rule, "An emoji was removed or is unavailable. Edit the rule to replace it."
                    )
                    had_error = True
                self.store.update_job(job["message"], position=index + 1)
            self.store.update_job(job["message"], done=1)
            if not had_error:
                self.store.error(rule, "")
        except discord.NotFound:
            self.store.update_job(job["message"], done=1)
        except (discord.Forbidden, ValueError) as exc:
            self.store.error(rule, str(exc))
            self.store.update_job(job["message"], done=1)
        except (discord.HTTPException, OSError, TimeoutError) as exc:
            attempts = job["attempts"] + 1
            self.store.update_job(
                job["message"],
                attempts=attempts,
                retry=time.time() + min(300, 5 * 2**attempts),
                done=int(attempts >= 5),
            )
            self.store.error(
                rule,
                "Discord could not add reactions; check permissions and emoji availability. "
                + str(exc)[:150],
            )

    async def worker(self):
        await self.client.wait_until_ready()
        next_prune = 0
        while not self.client.is_closed():
            try:
                for job in self.store.jobs():
                    await self.deliver(job)
                if time.time() >= next_prune:
                    self.store.prune()
                    next_prune = time.time() + 3600
            except Exception:
                logging.exception("AutoReact worker failed; will retry")
            await asyncio.sleep(2)


def service(client):
    if not hasattr(client, "autoreact_service"):
        client.autoreact_service = AutoReactService(client)
    return client.autoreact_service
