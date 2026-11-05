"""Saved custom posts, publishing and count-based sticky delivery."""

import asyncio
import logging
import time

import discord
from discord.ext import commands, tasks

from .model import render, validate
from .store import Store

log = logging.getLogger(__name__)


class CustomEmbeds(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.locks = {}
        self.recovered = set()
        self.retry = {}
        self.next_recovery = 0

    def lock(self, ident):
        return self.locks.setdefault(ident, asyncio.Lock())

    async def cog_load(self):
        self.store = await Store.open(
            self.bot.settings.database_path, self.bot.settings.database_url
        )
        self.worker.start()

    async def cog_unload(self):
        self.worker.cancel()
        task = self.worker.get_task()
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.store.close()

    async def authorize(self, interaction):
        if interaction.guild is None:
            raise ValueError("Open Manage Embeds inside your server.")
        member = await interaction.guild.fetch_member(interaction.user.id)
        if not member.guild_permissions.administrator:
            raise ValueError("Only server administrators can publish or delete custom embeds.")

    async def channel(self, guild, ident, *, publishing=True):
        channel = self.bot.get_channel(ident) or await self.bot.fetch_channel(ident)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild:
            raise ValueError("Choose a text channel in this server.")
        permissions = channel.permissions_for(channel.guild.me)
        needed = (
            "view_channel",
            "send_messages",
            "embed_links",
            "attach_files",
            "read_message_history",
        )
        if not publishing:
            needed = ("view_channel",)
        missing = [key.replace("_", " ") for key in needed if not getattr(permissions, key)]
        if missing:
            raise ValueError("KY BOT needs these channel permissions: " + ", ".join(missing) + ".")
        return channel

    async def post(self, channel, data, *, message=None, private=False):
        view, attachments = await asyncio.to_thread(render, data)
        try:
            if message:
                return await message.edit(
                    content=None,
                    embeds=[],
                    view=view,
                    attachments=attachments,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return await channel.send(
                view=view,
                files=attachments,
                **({"ephemeral": True} if private else {}),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            for attachment in attachments:
                attachment.close()
            view.stop()

    async def clean(self, guild, ident):
        current = await self.store.get(guild, ident)
        obsolete = list(current["retired"])
        if current["deleted"] and current["message"]:
            obsolete.append(dict(channel=current["channel"], message=current["message"]))
        for item in obsolete:
            if not current["deleted"] and item == dict(
                channel=current["channel"], message=current["message"]
            ):
                await self.store.forget(guild, ident, item)
                continue
            try:
                channel = await self.channel(guild, item["channel"], publishing=False)
                await channel.get_partial_message(item["message"]).delete()
            except discord.NotFound:
                pass
            except (discord.HTTPException, ValueError):
                return False
            await self.store.forget(guild, ident, item)
        if current["deleted"]:
            await self.store.erase(guild, ident)
            self.recovered.discard(ident)
        return True

    async def publish(self, interaction, ident, revision):
        await self.authorize(interaction)
        guild = interaction.guild_id
        async with self.lock(ident):
            current = await self.store.get(guild, ident)
            if current["revision"] != revision or current["deleted"]:
                raise ValueError("This embed changed. Refresh the editor before publishing.")
            data = validate(current["draft"], publish=True)
            channel = await self.channel(guild, data["channel"])
            # Clear an earlier failed move before creating any further copies.
            if not await self.clean(guild, ident):
                raise ValueError(
                    "The previous copy could not be removed. Check KY BOT’s "
                    "access to its old channel and try again."
                )
            message = None
            if current["message"] and current["channel"] == channel.id:
                try:
                    message = await channel.fetch_message(current["message"])
                except discord.NotFound:
                    pass
            result = await self.post(channel, data, message=message)
            try:
                await self.store.published(
                    guild, ident, current["message"], data, channel.id, result.id
                )
            except BaseException:
                if message is None:
                    try:
                        await result.delete()
                    except discord.HTTPException:
                        log.warning("Unsaved custom embed could not be removed: %s", ident)
                raise
            self.recovered.add(ident)
            removed = await self.clean(guild, ident)
            return "Embed published / updated." + (
                ""
                if removed
                else " The old copy is queued for removal; check access to its channel."
            )

    async def move(self, guild, ident):
        async with self.lock(ident):
            current = await self.store.get(guild, ident)
            if current["deleted"]:
                await self.clean(guild, ident)
                return
            if not await self.clean(guild, ident):
                raise ValueError("The old custom embed copy could not be removed yet.")
            current = await self.store.get(guild, ident)
            if current["deleted"] or not current["published"] or not current["sticky_every"]:
                return
            if current["message_count"] < current["sticky_every"]:
                return
            channel = await self.channel(guild, current["channel"])
            latest = [m async for m in channel.history(limit=1)]
            if latest and latest[0].id == current["message"]:
                await self.store.acknowledge(guild, ident)
                return
            result = await self.post(channel, current["published"])
            try:
                await self.store.published(
                    guild,
                    ident,
                    current["message"],
                    current["published"],
                    channel.id,
                    result.id,
                    moved=True,
                    consumed=current["message_count"],
                )
            except BaseException:
                try:
                    await result.delete()
                except discord.HTTPException:
                    log.warning("Unsaved sticky embed could not be removed: %s", ident)
                raise
            await self.clean(guild, ident)

    @commands.Cog.listener()
    async def on_message(self, message):
        if (
            not message.guild
            or isinstance(message.channel, discord.Thread)
            or message.author.id == self.bot.user.id
        ):
            return
        for (ident,) in await self.store.targets(message.guild.id, message.channel.id):
            async with self.lock(ident):
                await self.store.note_one(message.guild.id, ident, message.id)

    async def recover(self, guild, ident):
        async with self.lock(ident):
            current = await self.store.get(guild, ident)
            if not current["sticky_every"] or current["deleted"]:
                return
            channel = await self.channel(guild, current["channel"])
            count = current["message_count"]
            # Stop once the threshold is reached; a new post will sit below the remaining history.
            async for message in channel.history(
                after=discord.Object(id=current["last_seen"]), oldest_first=True, limit=None
            ):
                if count >= current["sticky_every"]:
                    break
                if message.author.id != self.bot.user.id:
                    await self.store.note_one(guild, ident, message.id)
                    count += 1
            self.recovered.add(ident)

    async def tick(self):
        now = time.monotonic()
        if now >= self.next_recovery:
            for guild, ident in await self.store.sticky_index():
                if ident not in self.recovered:
                    try:
                        await self.recover(guild, ident)
                    except (discord.HTTPException, ValueError):
                        log.warning("Custom embed counter recovery will retry: %s", ident)
            self.next_recovery = now + 30
        for guild, ident in await self.store.due():
            if self.retry.get(ident, 0) > now:
                continue
            try:
                row = await self.store.get(guild, ident)
                if row["deleted"]:
                    async with self.lock(ident):
                        if not await self.clean(guild, ident):
                            raise ValueError("Message removal will retry.")
                else:
                    await self.move(guild, ident)
                self.retry.pop(ident, None)
            except (discord.HTTPException, ValueError):
                self.retry[ident] = time.monotonic() + 10
                log.warning("Custom embed update queued for retry: %s", ident)

    @tasks.loop(seconds=2)
    async def worker(self):
        try:
            await self.tick()
        except Exception:
            log.exception("Custom embed update cycle failed; will retry")

    @worker.before_loop
    async def ready(self):
        await self.bot.wait_until_ready()

    async def open_editor(self, interaction):
        from .views import Session, initial_channel

        await self.authorize(interaction)
        await interaction.response.defer(ephemeral=True, thinking=True)
        ident = await self.store.create(interaction.guild_id, initial_channel(interaction))
        await Session(self, interaction.user.id, interaction.guild_id, ident, creating=True).show(
            interaction
        )

    async def open_menu(self, interaction, *, command_center=None):
        if command_center is None or not await command_center.interaction_check(interaction):
            return
        from .views import Library, editor_artwork

        await interaction.response.defer()
        view = Library(
            self,
            interaction.user.id,
            interaction.guild_id,
            await self.store.listing(interaction.guild_id),
            parent=command_center,
        )
        attachments = editor_artwork()
        try:
            if command_center.panel_manager:
                await command_center.panel_manager.edit(
                    command_center, interaction, view, attachments
                )
            else:
                await interaction.edit_original_response(
                    content=None,
                    embeds=[],
                    view=view,
                    attachments=attachments,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        finally:
            for file in attachments:
                file.close()


async def setup(bot):
    await bot.add_cog(CustomEmbeds(bot))
