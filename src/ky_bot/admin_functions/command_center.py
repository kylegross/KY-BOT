"""Durable channel panels with administrator sessions and a ten-minute idle reset."""

import asyncio
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, replace

import discord
from discord.ext import tasks

from ky_bot.errors import send_private

log = logging.getLogger(__name__)
IDLE_SECONDS = 600


def command_center_link(message):
    view = discord.ui.LayoutView(timeout=180)
    view.add_item(discord.ui.TextDisplay(
        f"Command Center is available in <#{message.channel.id}>."
    ))
    view.add_item(discord.ui.ActionRow(discord.ui.Button(
        label="Open Command Center", url=message.jump_url
    )))
    return view


@dataclass
class Panel:
    message: discord.Message
    view: discord.ui.LayoutView
    token: str
    touched: float
    owner_id: int | None = None


class CommandCenterPanels:
    def __init__(self, bot):
        self.bot = bot
        self.entries = {}
        self.locks = {}
        self.retired_messages = deque(maxlen=1000)

    def owns_message(self, guild_id, message_id):
        entry = self.entries.get(guild_id)
        return bool(entry and entry.message.id == message_id) or (
            guild_id, message_id
        ) in self.retired_messages

    def lock(self, guild_id):
        return self.locks.setdefault(guild_id, asyncio.Lock())

    def start(self):
        self.sweep.start()

    def close(self):
        self.sweep.cancel()
        for entry in self.entries.values():
            entry.view.stop()

    async def permitted_channel(self, guild, channel_id):
        channel = await self.bot.server_settings.validate_channel(
            guild, channel_id, require_images=True
        )
        if not channel.permissions_for(guild.me).read_message_history:
            raise ValueError(
                "I need Read Message History to restore the Command Center after restart."
            )
        return channel

    async def landing(self, guild, token, *, channel_id=None):
        from ky_bot.admin_functions.settings_view import SettingsView
        from ky_bot.design.menu_design import profile_for

        service = self.bot.server_settings
        view = SettingsView(None, guild.id, service, panel_manager=self, panel_token=token)
        view.guild = guild
        view.settings = await service.repository.get(guild.id)
        if channel_id is not None:
            view.settings = replace(view.settings, command_center_channel_id=channel_id)
        _, profiles = await service.repository.get_designs(guild.id)
        if "settings:overview" in profiles:
            view.design = profile_for(profiles, "settings:overview")
        view.show_section("overview")
        return view

    async def publish(self, guild, channel_id):
        from ky_bot.admin_functions.settings_view import settings_files

        async with self.lock(guild.id):
            channel = await self.permitted_channel(guild, channel_id)
            saved = await self.bot.server_settings.repository.get(guild.id)
            old = self.entries.get(guild.id)
            old_message = old.message if old else None
            if old_message is None and saved.command_center_channel_id:
                try:
                    old_channel = await self.permitted_channel(
                        guild, saved.command_center_channel_id
                    )
                    old_message = await old_channel.fetch_message(saved.command_center_message_id)
                except (ValueError, discord.HTTPException):
                    pass
            if old_message and old_message.channel.id == channel_id:
                if old is None:
                    self.entries[guild.id] = Panel(
                        old_message, discord.ui.LayoutView(timeout=None), "", time.monotonic()
                    )
                try:
                    await self._reset(guild.id)
                except BaseException:
                    if old is None:
                        self.entries.pop(guild.id).view.stop()
                    raise
                return self.entries[guild.id].message
            token = uuid.uuid4().hex
            view = await self.landing(guild, token, channel_id=channel_id)
            uploads = settings_files("overview", view.design)
            message = None
            try:
                message = await channel.send(
                    view=view, files=uploads, allowed_mentions=discord.AllowedMentions.none()
                )
                await self.bot.server_settings.repository.save_command_center(
                    guild.id, channel.id, message.id
                )
            except BaseException:
                view.stop()
                if message:
                    await message.delete()
                raise
            finally:
                for upload in uploads:
                    upload.close()
            view.message = message
            self.entries[guild.id] = Panel(message, view, token, time.monotonic())
            if old:
                old.view.stop()
            if old_message:
                self.retired_messages.append((guild.id, old_message.id))
                try:
                    await old_message.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException:
                    # Even if removal fails, its old token cannot change this panel.
                    log.warning("Could not remove previous Command Center in guild %s", guild.id)
            return message

    async def allowed(self, parent, interaction):
        entry = self.entries.get(parent.guild_id)
        if (
            entry is None or entry.token != parent.panel_token
            or interaction.guild_id != parent.guild_id or interaction.guild is None
            or not interaction.permissions.administrator
        ):
            await send_private(interaction, "Use the current Command Center as an administrator.")
            return False
        if entry.owner_id not in (None, interaction.user.id):
            await send_private(
                interaction,
                "Another administrator is using this panel. It becomes available when they "
                "return Home or after ten minutes without interaction.",
            )
            return False
        entry.owner_id = parent.owner_id = interaction.user.id
        entry.touched = time.monotonic()
        return True

    async def edit(self, parent, interaction, view, uploads):
        async with self.lock(parent.guild_id):
            entry = self.entries.get(parent.guild_id)
            if entry is None or entry.token != parent.panel_token:
                view.stop()
                await send_private(interaction, "The panel returned home. Open your menu again.")
                return None
            view.timeout = None
            try:
                message = await entry.message.edit(
                    content=None, embeds=[], view=view, attachments=uploads,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            except BaseException:
                view.stop()
                raise
            old_view = entry.view
            entry.message, entry.view = message, view
            entry.touched = time.monotonic()
            view.message = message
            old_view.stop()
            return message

    async def _reset(self, guild_id, *, due_only=False):
        from ky_bot.admin_functions.settings_view import settings_files

        entry = self.entries[guild_id]
        if due_only and (entry.owner_id is None or time.monotonic() - entry.touched < IDLE_SECONDS):
            return
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return
        touched = entry.touched
        token = uuid.uuid4().hex
        view = await self.landing(guild, token)
        uploads = settings_files("overview", view.design)
        try:
            if due_only and entry.touched != touched:
                view.stop()
                return
            message = await entry.message.edit(
                content=None, embeds=[], view=view, attachments=uploads,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except BaseException:
            view.stop()
            raise
        finally:
            for upload in uploads:
                upload.close()
        entry.view.stop()
        view.message = message
        self.entries[guild_id] = Panel(message, view, token, time.monotonic())

    async def reset(self, guild_id, *, due_only=False):
        async with self.lock(guild_id):
            if guild_id in self.entries:
                await self._reset(guild_id, due_only=due_only)

    async def tick(self):
        repository = self.bot.server_settings.repository
        for guild_id, channel_id, message_id in await repository.command_centers():
            try:
                if guild_id not in self.entries:
                    async with self.lock(guild_id):
                        if guild_id in self.entries:
                            continue
                        guild = self.bot.get_guild(guild_id)
                        if guild is None:
                            continue
                        channel = await self.permitted_channel(guild, channel_id)
                        message = await channel.fetch_message(message_id)
                        self.entries[guild_id] = Panel(
                            message, discord.ui.LayoutView(timeout=None), "", time.monotonic()
                        )
                        try:
                            await self._reset(guild_id)
                        except BaseException:
                            self.entries.pop(guild_id).view.stop()
                            raise
                else:
                    await self.reset(guild_id, due_only=True)
            except discord.NotFound:
                entry = self.entries.pop(guild_id, None)
                if entry:
                    entry.view.stop()
                await repository.clear_command_center(guild_id)
            except (ValueError, discord.HTTPException):
                # A temporary permission/API failure must not erase the saved location.
                log.warning("Command Center unavailable in guild %s; retrying later", guild_id)

    @tasks.loop(seconds=10)
    async def sweep(self):
        try:
            await self.tick()
        except Exception:
            log.exception("Command Center refresh failed; retrying on the next pass")

    @sweep.before_loop
    async def before_sweep(self):
        await self.bot.wait_until_ready()
