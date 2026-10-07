"""Private server settings arranged as inline Components V2 sections."""

import io
from importlib.resources import files

import discord

from ky_bot.database.settings import GuildSettings
from ky_bot.errors import report_error, send_private
from ky_bot.services.settings import SETTINGS_TITLES, SettingsService

HEADER_URL = "attachment://ky_settings_header.png"


def settings_title_file(section: str) -> discord.File:
    return discord.File(
        files("ky_bot").joinpath("assets", "header", SETTINGS_TITLES[section][1]).open("rb"),
        filename="ky_settings_title.png",
    )


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
        panel = discord.ui.Container(accent_colour=0xC9DDF0)
        panel.add_item(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(
                    HEADER_URL,
                    description="KY BOT • COMMAND CENTER",
                )
            )
        )
        panel.add_item(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(
                    "attachment://ky_settings_title.png",
                    description=SETTINGS_TITLES[section][0],
                )
            )
        )
        if notice:
            panel.add_item(discord.ui.TextDisplay(notice))

        def row(text: str, button: discord.ui.Button) -> None:
            panel.add_item(discord.ui.Section(text, accessory=button))

        def separator() -> None:
            panel.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        def channel(channel_id: int | None) -> str:
            if self.guild is None:
                return f"<#{channel_id}>" if channel_id else "Not configured"
            return channel_label(channel_id, self.guild)

        if section == "overview":
            status = "Enabled" if self.settings.welcome_channel_id else "Disabled"
            row(
                f"**WELCOME EXPERIENCE**\n-# Personal greetings for new members · {status}",
                self.navigation("Manage", "welcome"),
            )
            separator()
            row(
                "**SERVER ACTIVITY**\n-# Moderation logs · Coming soon",
                self.navigation("Manage", "logging"),
            )
        elif section == "welcome":
            row(
                f"**DESTINATION**\n-# {channel(self.settings.welcome_channel_id)}",
                self.navigation("Choose channel", section, picker=True),
            )
            if picker:
                self.welcome_channel = discord.ui.ChannelSelect(
                    channel_types=[discord.ChannelType.text],
                    placeholder="Choose a welcome channel…",
                    min_values=1,
                    max_values=1,
                )
                self.welcome_channel.callback = self.save_welcome_channel
                panel.add_item(discord.ui.ActionRow(self.welcome_channel))
            separator()
            # Escape user-authored Markdown in the configuration display only.
            text = discord.utils.escape_markdown(self.settings.welcome_message).replace(
                "\n", "\n-# "
            )
            self.edit_welcome = self.button("Edit message", self.open_welcome_editor)
            row(f"**WELCOME MESSAGE**\n-# {text}", self.edit_welcome)
            panel.add_item(
                discord.ui.TextDisplay(
                    "-# Use {member} to mention the new member and {server} for the server name."
                )
            )
            separator()
            self.disable_welcome = self.button("Disable", self.turn_off_welcomes)
            self.disable_welcome.disabled = self.settings.welcome_channel_id is None
            row(
                "**DELIVERY**\n-# "
                + (
                    "Enabled — sent when someone joins."
                    if self.settings.welcome_channel_id
                    else "Disabled — choose a channel to enable."
                ),
                self.disable_welcome,
            )
        elif section == "welcome_design":
            background = (
                "Your uploaded image"
                if self.settings.welcome_custom_background
                else "Silver floral"
            )
            row(
                f"**BACKGROUND**\n-# {background}",
                self.button("Upload image", self.upload_welcome_background),
            )
            separator()
            row(
                f"**APPEARANCE**\n-# {self.settings.welcome_title} · "
                f"#{self.settings.welcome_accent} · Darkening {self.settings.welcome_dim}%",
                self.button("Edit style", self.edit_welcome_style),
            )
            separator()
            row(
                "**DEFAULT BACKGROUND**\n-# Restore the silver floral artwork.",
                self.button("Restore", self.reset_welcome_background),
            )
        else:
            panel.add_item(
                discord.ui.TextDisplay(
                    "-# Coming soon · Save a destination for future moderation logs."
                )
            )
            separator()
            row(
                f"**DESTINATION**\n-# {channel(self.settings.log_channel_id)}",
                self.navigation("Choose channel", section, picker=True),
            )
            if picker:
                self.log_channel = discord.ui.ChannelSelect(
                    channel_types=[discord.ChannelType.text],
                    placeholder="Choose a log channel…",
                    min_values=1,
                    max_values=1,
                )
                self.log_channel.callback = self.save_log_channel
                panel.add_item(discord.ui.ActionRow(self.log_channel))
            separator()
            self.clear_channel = self.button("Clear channel", self.clear_log_channel)
            self.clear_channel.disabled = self.settings.log_channel_id is None
            row("**SAVED DESTINATION**\n-# Remove the selected channel.", self.clear_channel)

        separator()
        controls = discord.ui.ActionRow()
        if section != "overview":
            controls.add_item(
                self.navigation("Back", "welcome" if section == "welcome_design" else "overview")
            )
        if section == "welcome":
            controls.add_item(self.navigation("Design", "welcome_design"))
        if section in {"welcome", "welcome_design"}:
            controls.add_item(self.button("Preview", self.preview_welcome))
        controls.add_item(self.button("Refresh", self.reload_settings))
        controls.add_item(self.button("Close", self.close_menu))
        panel.add_item(
            discord.ui.TextDisplay(
                "-# Private admin controls · Saved automatically · 3-minute session"
            )
        )
        panel.add_item(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem(
                    "attachment://ky_settings_footer.png",
                    description="KY BOT floral wreath footer",
                )
            )
        )
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
        attachments = self.message.attachments if self.message is not None else []
        retained = [
            attachment
            for attachment in attachments
            if attachment.filename in {"ky_settings_header.png", "ky_settings_footer.png"}
        ]
        self.message = await interaction.edit_original_response(
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
            attachments=[*retained, settings_title_file(self.section)],
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
            interaction,
            "Welcome channel saved. Welcome messages are enabled."
            if welcome
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

    async def upload_welcome_background(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(WelcomeBackgroundModal(self))

    async def edit_welcome_style(self, interaction: discord.Interaction) -> None:
        settings = await self.service.repository.get(self.guild_id)
        await interaction.response.send_modal(WelcomeStyleModal(self, settings))

    async def reset_welcome_background(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self.service.repository.set_welcome_background(self.guild_id, None)
        await self.refresh(interaction, "Silver floral background restored.")

    async def preview_welcome(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        image = await self.service.render_welcome_card(interaction.user)
        await interaction.followup.send(
            "Preview using your profile; new members will see their own avatar and name.",
            file=discord.File(io.BytesIO(image), filename="welcome-preview.png"),
            ephemeral=True,
        )

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
            style=discord.TextStyle.paragraph,
            default=message,
            max_length=1500,
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


class WelcomeDesignModal(discord.ui.Modal):
    def __init__(self, menu: SettingsView, *, title: str) -> None:
        super().__init__(title=title, timeout=180)
        self.menu = menu

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await self.menu.interaction_check(interaction)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await report_error(interaction, error)


class WelcomeBackgroundModal(WelcomeDesignModal):
    def __init__(self, menu: SettingsView) -> None:
        super().__init__(menu, title="Upload welcome background")
        self.upload = discord.ui.FileUpload(min_values=1, max_values=1)
        self.add_item(
            discord.ui.Label(
                text="Background image",
                description="Still PNG, JPEG or WebP · Up to 8 MB",
                component=self.upload,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        from ky_bot.services.footers import MAX_UPLOAD

        attachment = self.upload.values[0]
        if attachment.size > MAX_UPLOAD:
            await send_private(interaction, "Use an image under 8 MB.")
            return
        try:
            await self.menu.service.set_welcome_background(
                self.menu.guild_id, await attachment.read()
            )
        except ValueError as error:
            await send_private(interaction, str(error))
            return
        await self.menu.refresh(
            interaction, "Background saved. Use Preview to see your welcome card."
        )


class WelcomeStyleModal(WelcomeDesignModal):
    def __init__(self, menu: SettingsView, settings: GuildSettings) -> None:
        super().__init__(menu, title="Welcome image appearance")
        self.heading = discord.ui.TextInput(
            label="Title",
            default=settings.welcome_title,
            max_length=48,
        )
        self.accent = discord.ui.TextInput(
            label="Accent color (hex)",
            default="#" + settings.welcome_accent,
            max_length=7,
        )
        self.darkening = discord.ui.TextInput(
            label="Background darkening (0–80)",
            default=str(settings.welcome_dim),
            max_length=2,
        )
        for field in (self.heading, self.accent, self.darkening):
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        try:
            await self.menu.service.set_welcome_appearance(
                self.menu.guild_id, self.heading.value, self.accent.value, self.darkening.value
            )
        except ValueError as error:
            await send_private(interaction, str(error))
            return
        await self.menu.refresh(interaction, "Welcome appearance saved.")
