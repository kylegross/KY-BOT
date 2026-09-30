"""Shared safe responses for command and component failures."""

import logging
import uuid

import discord
from discord import app_commands

log = logging.getLogger(__name__)


async def send_private(interaction: discord.Interaction, message: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        log.warning("Could not deliver error response for interaction %s", interaction.id)


async def report_error(interaction: discord.Interaction, error: Exception) -> None:
    if isinstance(error, app_commands.CommandOnCooldown):
        message = f"Please try again in {error.retry_after:.0f} seconds."
    elif isinstance(error, app_commands.BotMissingPermissions):
        message = (
            "I’m missing permissions needed for that action. Ask a server admin to review them."
        )
    elif isinstance(error, app_commands.CheckFailure):
        message = "You can’t use that action here, or you don’t have the required permissions."
    else:
        original = getattr(error, "original", error)
        reference = uuid.uuid4().hex[:8]
        log.error(
            "Interaction failed [ref=%s, interaction=%s]",
            reference,
            interaction.id,
            exc_info=(type(original), original, original.__traceback__),
        )
        message = f"Something went wrong. Please try again. Reference: `{reference}`"
    await send_private(interaction, message)


class CommandTree(app_commands.CommandTree):
    async def on_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        await report_error(interaction, error)
