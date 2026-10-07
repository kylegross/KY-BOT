"""Validation for stored server configuration."""

import asyncio
import re

import discord

from ky_bot.database.settings import SettingsRepository

SETTINGS_TITLES = {
    "overview": ("SERVER SETTINGS", "ky_settings_title.png"),
    "welcome": ("WELCOME SETTINGS", "ky_settings_welcome_title.png"),
    "logging": ("SERVER ACTIVITY", "ky_settings_logging_title.png"),
    "welcome_design": ("WELCOME IMAGE DESIGN", "ky_settings_design_title.png"),
}


class SettingsService:
    def __init__(self, repository: SettingsRepository) -> None:
        self.repository = repository

    async def set_welcome_background(self, guild_id: int, data: bytes) -> None:
        from ky_bot.services.welcome import normalize_background

        background = await asyncio.to_thread(normalize_background, data)
        await self.repository.set_welcome_background(guild_id, background)

    async def set_welcome_appearance(
        self, guild_id: int, title: str, accent: str, dim: str
    ) -> None:
        title = title.strip()
        accent = accent.strip().lstrip("#").upper()
        if not title or len(title) > 48 or any(ord(char) < 32 for char in title):
            raise ValueError("Use a single-line title with 1–48 characters.")
        if not re.fullmatch(r"[0-9A-F]{6}", accent):
            raise ValueError("Enter a six-digit color, such as #D8BB78.")
        try:
            darkening = int(dim)
        except ValueError:
            raise ValueError("Background darkening must be a number from 0 to 80.") from None
        if not 0 <= darkening <= 80:
            raise ValueError("Background darkening must be a number from 0 to 80.")
        await self.repository.set_welcome_appearance(guild_id, title, accent, darkening)

    async def set_log_channel(self, guild: discord.Guild, channel_id: int) -> None:
        channel = await self.validate_channel(guild, channel_id)
        await self.repository.set_log_channel(guild.id, channel.id)

    async def set_welcome_channel(self, guild: discord.Guild, channel_id: int) -> None:
        channel = await self.validate_channel(guild, channel_id, require_images=True)
        await self.repository.set_welcome_channel(guild.id, channel.id)

    async def validate_channel(
        self, guild: discord.Guild, channel_id: int, *, require_images: bool = False
    ) -> discord.TextChannel:
        channel = guild.get_channel(channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(channel_id)
            except discord.NotFound:
                raise ValueError(
                    "That channel no longer exists. Choose another text channel."
                ) from None
            except discord.Forbidden:
                raise ValueError(
                    "I can’t access that channel. Give me View Channel permission and try again."
                ) from None
            except discord.InvalidData:
                raise ValueError("Choose a text channel belonging to this server.") from None
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild.id:
            raise ValueError("Choose an existing text channel in this server.")
        if guild.me is None:
            raise ValueError("I can’t check my server permissions right now. Please try again.")
        permissions = channel.permissions_for(guild.me)
        if not (permissions.view_channel and permissions.send_messages and permissions.embed_links):
            raise ValueError("I need View Channel, Send Messages, and Embed Links in that channel.")
        if require_images and not permissions.attach_files:
            raise ValueError(
                "I need Attach Files permission to send welcome images in that channel."
            )
        return channel

    async def render_welcome_card(self, member: discord.Member) -> bytes:
        from ky_bot.services.welcome import render_welcome

        settings = await self.repository.get(member.guild.id)
        artwork = await self.repository.get_welcome_artwork(member.guild.id)
        try:
            avatar = await member.display_avatar.with_format("png").with_size(256).read()
        except discord.HTTPException:
            avatar = None
        values = {"member": member.display_name, "server": member.guild.name}
        message = re.sub(
            r"\{(member|server)\}", lambda match: values[match[1]], settings.welcome_message
        )
        return await asyncio.to_thread(
            render_welcome,
            artwork,
            member_name=member.display_name,
            server_name=member.guild.name,
            message=message,
            avatar=avatar,
        )

    async def set_welcome_message(self, guild_id: int, message: str) -> None:
        if not message.strip() or len(message) > 1500:
            raise ValueError("Enter a welcome message between 1 and 1,500 characters.")
        await self.repository.set_welcome_message(guild_id, message)
