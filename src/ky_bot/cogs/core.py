"""The first slash-command entry points."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ky_bot.views.help import HelpView, help_embed

if TYPE_CHECKING:
    from ky_bot.bot import KYBot


class Core(commands.Cog):
    def __init__(self, bot: "KYBot") -> None:
        self.bot = bot

    @app_commands.command(name="help", description="Explore KY BOT and its available tools.")
    @app_commands.guild_only()
    async def help_command(self, interaction: discord.Interaction) -> None:
        view = HelpView(interaction.user.id)
        await interaction.response.send_message(embed=help_embed(), view=view, ephemeral=True)
        view.message = await interaction.original_response()

    @app_commands.command(name="ping", description="Check whether KY BOT is responding.")
    @app_commands.guild_only()
    async def ping(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            f"Pong! Gateway heartbeat latency: **{self.bot.latency * 1000:.0f} ms**.",
            ephemeral=True,
        )


async def setup(bot: "KYBot") -> None:
    await bot.add_cog(Core(bot))
