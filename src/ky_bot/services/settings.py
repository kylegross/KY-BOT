"""Validation for stored server configuration."""

import discord

from ky_bot.database.settings import SettingsRepository


class SettingsService:
    def __init__(self, repository: SettingsRepository) -> None:
        self.repository = repository

    async def set_log_channel(self, guild: discord.Guild, channel_id: int) -> None:
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
        await self.repository.set_log_channel(guild.id, channel.id)
