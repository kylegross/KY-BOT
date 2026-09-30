"""Private server configuration with authorization checked on every interaction."""

import discord

from ky_bot.database.settings import GuildSettings
from ky_bot.errors import send_private
from ky_bot.services.settings import SettingsService
from ky_bot.views.base import OwnerView


def settings_embed(settings: GuildSettings, guild: discord.Guild) -> discord.Embed:
    channel_id = settings.log_channel_id
    channel = guild.get_channel(channel_id) if channel_id else None
    value = "Not configured"
    if channel_id:
        value = f"<#{channel_id}>" if channel else f"<#{channel_id}> (reselect to verify access)"
    embed = discord.Embed(
        title="Server settings",
        description="Choose a log channel to prepare for future moderation features. "
        "**Logging is not active yet.** Changes are saved immediately.",
        colour=0x8B5CF6,
    )
    embed.add_field(name="Log channel", value=value, inline=False)
    embed.set_footer(text="Administrators only • Menu expires after 3 minutes of inactivity")
    return embed


class SettingsView(OwnerView):
    def __init__(self, owner_id: int, guild_id: int, service: SettingsService) -> None:
        super().__init__(owner_id)
        self.guild_id = guild_id
        self.service = service

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.user.id != self.owner_id
            or interaction.guild_id != self.guild_id
            or interaction.guild is None
            or not interaction.permissions.administrator
        ):
            await send_private(
                interaction,
                "Only the administrator who opened this menu can use it. "
                "Run `/settings` to open your own menu.",
            )
            return False
        return True

    async def refresh(self, interaction: discord.Interaction, notice: str | None = None) -> None:
        settings = await self.service.repository.get(self.guild_id)
        await interaction.edit_original_response(
            content=notice, embed=settings_embed(settings, interaction.guild), view=self
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Choose a log channel…",
        min_values=1,
        max_values=1,
    )
    async def log_channel(
        self, interaction: discord.Interaction, select: discord.ui.ChannelSelect
    ) -> None:
        await interaction.response.defer()
        try:
            await self.service.set_log_channel(interaction.guild, select.values[0].id)
        except ValueError as error:
            await send_private(interaction, str(error))
            return
        await self.refresh(
            interaction, "Log channel saved. Logging will be added in a future module."
        )

    @discord.ui.button(label="Clear log channel", style=discord.ButtonStyle.secondary)
    async def clear_channel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        await self.service.repository.clear_log_channel(self.guild_id)
        await self.refresh(interaction, "Log channel cleared.")

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary)
    async def reload_settings(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        await self.refresh(interaction)

    @discord.ui.button(label="Close", style=discord.ButtonStyle.secondary)
    async def close_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="Settings closed. Use `/settings` to reopen.", embed=None, view=None
        )
        self.stop()
