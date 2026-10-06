"""Private server configuration with authorization checked on every interaction."""

import discord

from ky_bot.database.settings import GuildSettings
from ky_bot.errors import send_private
from ky_bot.services.settings import SettingsService
from ky_bot.views.base import OwnerView


def settings_embed(
    settings: GuildSettings, guild: discord.Guild, section: str = "overview"
) -> discord.Embed:
    channel_id = settings.log_channel_id
    channel = guild.get_channel(channel_id) if channel_id else None
    value = "Not configured"
    if channel_id:
        value = f"<#{channel_id}>" if channel else f"<#{channel_id}> (reselect to verify access)"
    embed = discord.Embed(
        title="Server settings",
        description="Configure KY BOT for this server. Changes are saved immediately.",
        colour=0x8B5CF6,
    )
    embed.add_field(name="Log channel", value=value, inline=False)
    embed.add_field(
        name="Welcome channel",
        value=f"<#{settings.welcome_channel_id}>" if settings.welcome_channel_id else "Disabled",
        inline=False,
    )
    embed.add_field(name="Welcome message", value=settings.welcome_message[:1024], inline=False)
    embed.add_field(
        name="Personalize your message",
        value="Use `{member}` for the new member and `{server}` for the server name. "
        "Logging is planned and is not active yet.", inline=False,
    )
    embed.set_footer(text="Administrators only • Menu expires after 3 minutes of inactivity")
    embed.set_author(name="KY BOT • Command Center")
    embed.set_image(url="attachment://ky_settings_header.png")
    if section == "overview":
        embed.clear_fields()
        embed.description = "Your server. Your settings.\nChoose a section below to get started."
        embed.add_field(
            name="👋 Welcome", value="Enabled" if settings.welcome_channel_id else "Disabled",
            inline=True,
        )
        embed.add_field(name="📋 Logging", value="Coming soon", inline=True)
    elif section == "welcome":
        embed.title = "Welcome settings"
        embed.remove_field(0)
        embed.description = "Greet new members with a message made for this server."
        embed.set_field_at(
            2, name="Message placeholders",
            value="`{member}` mentions the new member · `{server}` adds the server name.",
            inline=False,
        )
    elif section == "logging":
        embed.title = "Logging settings"
        while len(embed.fields) > 1:
            embed.remove_field(1)
        embed.description = "Save a channel for future moderation logs. Logging is not active yet."
    return embed


class SettingsView(OwnerView):
    def __init__(self, owner_id: int, guild_id: int, service: SettingsService) -> None:
        super().__init__(owner_id)
        self.guild_id = guild_id
        self.service = service
        self.section = "overview"
        self.show_section("overview")

    def show_section(self, section: str, *, picker: bool = False) -> None:
        self.section = section
        self.clear_items()

        def navigation(label: str, target: str, *, pick: bool = False) -> None:
            button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, row=0)

            async def callback(interaction: discord.Interaction) -> None:
                await interaction.response.defer()
                self.show_section(target, picker=pick)
                await self.refresh(interaction)

            button.callback = callback
            self.add_item(button)

        if section == "overview":
            navigation("👋 Welcome", "welcome")
            navigation("📋 Logging", "logging")
        else:
            navigation("Choose channel", section, pick=True)
            if section == "welcome":
                self.edit_welcome.row = 0
                self.disable_welcome.row = 0
                self.add_item(self.edit_welcome)
                self.add_item(self.disable_welcome)
            else:
                self.clear_channel.row = 0
                self.add_item(self.clear_channel)
            navigation("Back", "overview")
            if picker:
                select = self.welcome_channel if section == "welcome" else self.log_channel
                select.row = 1
                self.add_item(select)
        self.reload_settings.row = 2
        self.close_menu.row = 2
        self.add_item(self.reload_settings)
        self.add_item(self.close_menu)

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
            content=notice,
            embed=settings_embed(settings, interaction.guild, self.section), view=self,
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

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Choose a welcome channel…",
        min_values=1, max_values=1, row=2,
    )
    async def welcome_channel(
        self, interaction: discord.Interaction, select: discord.ui.ChannelSelect
    ) -> None:
        await interaction.response.defer()
        try:
            await self.service.set_welcome_channel(interaction.guild, select.values[0].id)
        except ValueError as error:
            await send_private(interaction, str(error))
            return
        await self.refresh(interaction, "Welcome messages enabled in the selected channel.")

    @discord.ui.button(label="Edit welcome message", style=discord.ButtonStyle.primary, row=3)
    async def edit_welcome(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        settings = await self.service.repository.get(self.guild_id)
        await interaction.response.send_modal(WelcomeModal(self, settings.welcome_message))

    @discord.ui.button(label="Disable welcomes", style=discord.ButtonStyle.secondary, row=3)
    async def disable_welcome(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.defer()
        await self.service.repository.set_welcome_channel(self.guild_id, None)
        await self.refresh(interaction, "Welcome messages disabled. Your message is saved.")


class WelcomeModal(discord.ui.Modal, title="Edit welcome message"):
    def __init__(self, menu: SettingsView, message: str) -> None:
        super().__init__(timeout=180)
        self.menu = menu
        self.message_input = discord.ui.TextInput(
            label="Message ({member} and {server} supported)",
            style=discord.TextStyle.paragraph, default=message, max_length=1500,
        )
        self.add_item(self.message_input)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.menu.interaction_check(interaction)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            await self.menu.service.set_welcome_message(
                self.menu.guild_id, str(self.message_input.value)
            )
        except ValueError as error:
            await send_private(interaction, str(error))
            return
        await self.menu.refresh(interaction, "Welcome message saved.")

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        from ky_bot.errors import report_error

        await report_error(interaction, error)
