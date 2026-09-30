from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ky_bot.views.settings import SettingsView, settings_embed

if TYPE_CHECKING:
    from ky_bot.bot import KYBot


class ServerSettings(commands.Cog):
    def __init__(self, bot: "KYBot") -> None:
        self.bot = bot

    @app_commands.command(name="settings", description="Configure KY BOT for this server.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def settings_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        settings = await self.bot.server_settings.repository.get(interaction.guild_id)
        view = SettingsView(interaction.user.id, interaction.guild_id, self.bot.server_settings)
        await interaction.edit_original_response(
            embed=settings_embed(settings, interaction.guild), view=view
        )
        view.message = await interaction.original_response()


async def setup(bot: "KYBot") -> None:
    await bot.add_cog(ServerSettings(bot))
