"""Message earning and opt-in voice intervals; never award offline/backfilled activity."""

import asyncio
import hashlib
import logging
import time

import discord
from discord.ext import tasks

from ky_bot.leveling.artwork import card_file, level_card

log = logging.getLogger(__name__)


def role_allowed(guild, role):
    if (
        role is None
        or role.guild.id != guild.id
        or role.is_default()
        or role.managed
        or not guild.me
        or not guild.me.guild_permissions.manage_roles
        or role >= guild.me.top_role
    ):
        raise ValueError("Choose an unmanaged role below KY BOT, with Manage Roles enabled.")
    if (
        role.permissions.administrator
        or role.permissions.manage_guild
        or role.permissions.manage_roles
    ):
        raise ValueError("Level rewards cannot grant administrative roles.")
    return role


class LevelingService:
    def __init__(self, bot, store):
        self.bot, self.store = bot, store
        self.voice = {}

    def start(self):
        self.voice_tick.start()

    async def close(self):
        task = self.voice_tick.get_task()
        self.voice_tick.cancel()
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.store.close()

    def checklist(self, guild, channel):
        checklists = getattr(self.bot, "checklists", None)
        return bool(checklists and checklists.store.is_checklist_channel(guild, channel))

    async def on_message(self, message):
        if (
            not message.guild
            or message.author.bot
            or message.webhook_id
            or message.is_system()
            or not isinstance(message.author, discord.Member)
        ):
            return
        text = message.content.strip()
        if (
            text.startswith(("/", "!", "?", f"<@{self.bot.user.id}>", f"<@!{self.bot.user.id}>"))
            or (len(text) < 3 and not message.attachments)
            or self.checklist(message.guild.id, message.channel.id)
        ):
            return
        # Only a digest is retained, never message text; repeat spam does not earn XP.
        normalized = " ".join(text.casefold().split())
        if not normalized:
            normalized = ",".join(str(a.id) for a in message.attachments)
        digest = hashlib.sha256(normalized.encode()).hexdigest()
        now = time.time()
        # Replayed/backfilled events are ineligible.
        if abs(now - message.created_at.timestamp()) > 120:
            return
        result = await self.store.award(
            message.guild.id,
            message.author.id,
            "message",
            now,
            source=message.id,
            fingerprint=digest,
            channels=[message.channel.id, getattr(message.channel, "parent_id", None)],
            roles=[r.id for r in message.author.roles],
        )
        await self.after_award(message.author, result)

    async def resolve_member(self, guild, member_id):
        member = guild.get_member(member_id)
        if member is None:
            try:
                member = await guild.fetch_member(member_id)
            except discord.NotFound:
                raise ValueError("That member is no longer in this server.") from None
        if member.guild.id != guild.id or member.bot:
            raise ValueError("Choose a human member of this server.")
        return member

    async def adjust_member(self, guild, member_id, action, value, *, actor, operation):
        member = await self.resolve_member(guild, member_id)
        result = await self.store.adjust(
            guild.id, member.id, action, value, actor=actor, operation=operation
        )
        if not result["replayed"] and result["level"] > result["before"]:
            await self.after_award(member, result, announce=False)
        return result

    async def after_award(self, member, result, *, announce=True):
        if result is None:
            return
        config = result["config"]
        for level, role_id in config["rewards"].items():
            if int(level) > result["level"] or member.get_role(role_id):
                continue
            try:
                role = role_allowed(member.guild, member.guild.get_role(role_id))
                await member.add_roles(role, reason="KY BOT level reward", atomic=True)
            except (ValueError, discord.HTTPException):
                log.warning(
                    "Unable to grant leveling reward %s in guild %s", role_id, member.guild.id
                )
        if (
            not announce
            or result["level"] <= result["before"]
            or not config["announcement_channel"]
        ):
            return
        channel = member.guild.get_channel(config["announcement_channel"])
        if not isinstance(channel, discord.TextChannel):
            return
        try:
            permissions = channel.permissions_for(member.guild.me)
            if not (
                permissions.view_channel and permissions.send_messages and permissions.attach_files
            ):
                return
            image = await self.render_card(member, result["level"])
            upload = card_file(image)
            try:
                await channel.send(
                    content=f"{member.mention} just leveled up!",
                    file=upload,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            finally:
                upload.close()
        except (ValueError, OSError, discord.HTTPException):
            log.exception("Unable to deliver level-up card in guild %s", member.guild.id)

    async def render_card(self, member, level):
        try:
            avatar = await member.display_avatar.with_format("png").with_size(256).read()
        except discord.HTTPException:
            avatar = None
        return await asyncio.to_thread(
            level_card,
            member.display_name,
            level,
            discord.utils.utcnow().strftime("%b %d, %Y · %H:%M UTC"),
            avatar,
        )

    def eligible_voice(self, member, channel, config):
        state = member.voice
        return bool(
            config["enabled"]
            and config["voice_enabled"]
            and not member.bot
            and state
            and channel.id != member.guild.afk_channel_id
            and not (
                state.self_deaf or state.deaf or state.self_mute or state.mute or state.suppress
            )
            and channel.id not in config["excluded_channels"]
            and not {r.id for r in member.roles} & set(config["excluded_roles"])
            and not self.checklist(member.guild.id, channel.id)
        )

    def invalidate_voice(self, guild):
        self.voice = {key: value for key, value in self.voice.items() if key[0] != guild}

    def voice_changed(self, member, before, after):
        # Reset everyone in affected channels when participation changes, so solo,
        # muted, AFK, and partial intervals cannot become a full earning interval.
        channels = {c.id for state in (before, after) if (c := getattr(state, "channel", None))}
        self.voice = {
            key: value
            for key, value in self.voice.items()
            if not (key[0] == member.guild.id and value[0] in channels)
        }

    @tasks.loop(seconds=15)
    async def voice_tick(self):
        if not self.bot.is_ready():
            self.voice.clear()
            return
        active = set()
        now = time.monotonic()
        for guild in self.bot.guilds:
            try:
                revision, config = await self.store.config(guild.id)
                if not config["enabled"] or not config["voice_enabled"]:
                    self.invalidate_voice(guild.id)
                    continue
                for channel in guild.voice_channels:
                    people = [m for m in channel.members if self.eligible_voice(m, channel, config)]
                    if len(people) < 2:
                        continue
                    for member in people:
                        key = (guild.id, member.id)
                        active.add(key)
                        previous = self.voice.get(key)
                        if (
                            previous is None
                            or previous[0] != channel.id
                            or previous[1] != revision
                            or now - previous[3] > 45
                        ):
                            self.voice[key] = (channel.id, revision, now, now)
                            continue
                        self.voice[key] = (*previous[:3], now)
                        if now - previous[2] < config["voice_interval"]:
                            continue
                        result = await self.store.award(
                            guild.id,
                            member.id,
                            "voice",
                            time.time(),
                            channels=[channel.id],
                            roles=[r.id for r in member.roles],
                            expected=revision,
                        )
                        self.voice[key] = (channel.id, revision, now, now)
                        await self.after_award(member, result)
            except Exception:
                self.invalidate_voice(guild.id)
                log.exception("Voice XP interval failed in guild %s", guild.id)
        self.voice = {key: value for key, value in self.voice.items() if key in active}

    @voice_tick.before_loop
    async def before_voice(self):
        await self.bot.wait_until_ready()
