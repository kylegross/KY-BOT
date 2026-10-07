"""Private server settings arranged as inline Components V2 sections."""

import discord

from ky_bot.database.settings import GuildSettings
from ky_bot.errors import report_error, send_private
from ky_bot.services.settings import SettingsService

HEADER_URL = "attachment://ky_settings_header.png"


def channel_label(channel_id: int | None, guild: discord.Guild) -> str:
    if channel_id is None:
        return "Not configured"
    label = f"<#{channel_id}>"
    return label if guild.get_channel(channel_id) else f"{label} (reselect to verify access)"


class SettingsView(discord.ui.LayoutView):
    def __init__(self, owner_id: int, guild_id: int, service: SettingsService) -> None:
        super().__init__(timeout=180)
        self.owner_id = owner_id
        self.guild_id = guild_id
        self.service = service
        self.message: discord.InteractionMessage | None = None
        self.settings = GuildSettings(guild_id)
        self.guild: discord.Guild | None = None
        self.section = "overview"
        self.picker = False
        self.show_section("overview")

    def button(self, label: str, callback) -> discord.ui.Button:
        button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
        button.callback = callback
        return button

    def navigation(self, label: str, target: str, *, picker: bool = False) -> discord.ui.Button:
        async def callback(interaction: discord.Interaction) -> None:
            await interaction.response.defer()
            self.show_section(target, picker=picker)
            await self.refresh(interaction)

        return self.button(label, callback)

    def show_section(
        self, section: str, *, picker: bool = False, notice: str | None = None
    ) -> None:
        self.section, self.picker = section, picker
        self.clear_items()
        panel = discord.ui.Container(accent_colour=0xC4AD78)
        panel.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(
            HEADER_URL, description="KY BOT • COMMAND CENTER",
        )))
        title = {
            "overview": "SERVER CONFIGURATION",
            "welcome": "SERVER CONFIGURATION / WELCOME",
            "logging": "SERVER CONFIGURATION / LOGGING",
        }[section]
        if section == "overview":
            panel.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(
                "attachment://ky_settings_title.png", description="SERVER SETTINGS",
            )))
        else:
            panel.add_item(discord.ui.TextDisplay(f"-# {title}"))
        if notice:
            panel.add_item(discord.ui.TextDisplay(notice))

        def row(text: str, button: discord.ui.Button) -> None:
            panel.add_item(discord.ui.Section(text, accessory=button))

        def separator() -> None:
            panel.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

        def channel(channel_id: int | None) -> str:
            if self.guild is None:
                return f"<#{channel_id}>" if channel_id else "Not configured"
            return channel_label(channel_id, self.guild)

        if section == "overview":
            status = "Enabled" if self.settings.welcome_channel_id else "Disabled"
            row(
                f"### WELCOME EXPERIENCE\n"
                f"-# Personal greetings for new members · {status}",
                self.navigation("Manage", "welcome"),
            )
            separator()
            row(
                "### SERVER ACTIVITY\n-# Moderation logs · Coming soon",
                self.navigation("Manage", "logging"),
            )
        elif section == "welcome":
            row(
                f"### Destination\n{channel(self.settings.welcome_channel_id)}",
                self.navigation("Choose channel", section, picker=True),
            )
            if picker:
                self.welcome_channel = discord.ui.ChannelSelect(
                    channel_types=[discord.ChannelType.text],
                    placeholder="Choose a welcome channel…",
                    min_values=1, max_values=1,
                )
                self.welcome_channel.callback = self.save_welcome_channel
                panel.add_item(discord.ui.ActionRow(self.welcome_channel))
            separator()
            # Escape user-authored Markdown in the configuration display only.
            text = discord.utils.escape_markdown(self.settings.welcome_message)
            self.edit_welcome = self.button("Edit message", self.open_welcome_editor)
            row(f"### Welcome message\n{text}", self.edit_welcome)
            panel.add_item(discord.ui.TextDisplay(
                "-# Use {member} to mention the new member and {server} for the server name."
            ))
            separator()
            self.disable_welcome = self.button("Disable", self.turn_off_welcomes)
            self.disable_welcome.disabled = self.settings.welcome_channel_id is None
            row(
                "### Delivery\n-# "
                + ("Enabled — sent when someone joins." if self.settings.welcome_channel_id
                   else "Disabled — choose a channel to enable."),
                self.disable_welcome,
            )
        else:
            panel.add_item(discord.ui.TextDisplay(
                "-# Coming soon · Save a destination for future moderation logs."
            ))
            separator()
            row(
                f"### Destination\n{channel(self.settings.log_channel_id)}",
                self.navigation("Choose channel", section, picker=True),
            )
            if picker:
                self.log_channel = discord.ui.ChannelSelect(
                    channel_types=[discord.ChannelType.text], placeholder="Choose a log channel…",
                    min_values=1, max_values=1,
                )
                self.log_channel.callback = self.save_log_channel
                panel.add_item(discord.ui.ActionRow(self.log_channel))
            separator()
            self.clear_channel = self.button("Clear channel", self.clear_log_channel)
            self.clear_channel.disabled = self.settings.log_channel_id is None
            row("### Saved destination\n-# Remove the selected channel.", self.clear_channel)

        separator()
        controls = discord.ui.ActionRow()
        if section != "overview":
            controls.add_item(self.navigation("Back", "overview"))
        controls.add_item(self.button("Refresh", self.reload_settings))
        controls.add_item(self.button("Close", self.close_menu))
        panel.add_item(discord.ui.TextDisplay(
            "-# Private admin controls · Saved automatically · 3-minute session"
        ))
        panel.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(
            "attachment://ky_settings_footer.png", description="KY BOT floral wreath footer",
        )))
        panel.add_item(controls)
        self.add_item(panel)

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
        self.settings = await self.service.repository.get(self.guild_id)
        self.guild = interaction.guild
        self.show_section(self.section, picker=self.picker, notice=notice)
        await interaction.edit_original_response(
            view=self, allowed_mentions=discord.AllowedMentions.none()
        )

    async def save_log_channel(self, interaction: discord.Interaction) -> None:
        await self.save_channel(interaction, welcome=False)

    async def save_welcome_channel(self, interaction: discord.Interaction) -> None:
        await self.save_channel(interaction, welcome=True)

    async def save_channel(self, interaction: discord.Interaction, *, welcome: bool) -> None:
        await interaction.response.defer()
        select = self.welcome_channel if welcome else self.log_channel
        setter = self.service.set_welcome_channel if welcome else self.service.set_log_channel
        try:
            await setter(interaction.guild, select.values[0].id)
        except ValueError as error:
            await send_private(interaction, str(error))
            return
        self.picker = False
        await self.refresh(
            interaction, "Welcome channel saved. Welcome messages are enabled." if welcome
            else "Log channel saved. Logging is not active yet.",
        )

    async def clear_log_channel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.service.repository.clear_log_channel(self.guild_id)
        await self.refresh(interaction, "Log channel cleared.")

    async def reload_settings(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.refresh(interaction)

    async def close_menu(self, interaction: discord.Interaction) -> None:
        self.clear_items()
        self.add_item(discord.ui.TextDisplay("Settings closed. Use `/settings` to reopen."))
        await interaction.response.edit_message(view=self, attachments=[])
        self.stop()

    async def open_welcome_editor(self, interaction: discord.Interaction) -> None:
        settings = await self.service.repository.get(self.guild_id)
        await interaction.response.send_modal(WelcomeModal(self, settings.welcome_message))

    async def turn_off_welcomes(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.service.repository.set_welcome_channel(self.guild_id, None)
        await self.refresh(interaction, "Welcome messages disabled. Your message is saved.")

    async def on_timeout(self) -> None:
        for item in self.walk_children():
            if isinstance(item, (discord.ui.Button, discord.ui.ChannelSelect)):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await report_error(interaction, error)


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
        await report_error(interaction, error)
