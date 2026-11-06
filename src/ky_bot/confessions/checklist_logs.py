"""Shared-panel checklist log settings; private launches retain a detached context."""

import io
from importlib.resources import files

import discord

from ky_bot.errors import report_error, send_private
from ky_bot.views.media import NoAltMediaGalleryItem
from ky_bot.views.menu_context import defer_menu, display_menu, menu_parent
from ky_bot.views.menu_strips import add_strip, category_files
from ky_bot.views.sections import section_dividers

from .command import administrator


class ChecklistLogs(discord.ui.LayoutView):
    def __init__(self, service, owner, guild, *, parent=None, back_menu=None):
        super().__init__(timeout=600)
        self.service, self.owner, self.guild = service, owner, guild
        self.parent, self.back_menu = parent, back_menu
        self.channel, self.revision = 0, None
        self.boards = discord.ui.ChannelSelect(
            placeholder="Choose a checklist channel", channel_types=[discord.ChannelType.text]
        )
        self.logs = discord.ui.ChannelSelect(
            placeholder="Choose its log channel, or clear to disable",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1,
            disabled=True,
        )
        self.boards.callback, self.logs.callback = self.choose_board, self.choose_logs
        self.build()

    def build(self, notice=None):
        self.clear_items()
        panel = discord.ui.Container(accent_colour=0xD6E2F3)
        panel.add_item(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://checklist_logs_header.png"))
        )
        if notice:
            panel.add_item(discord.ui.TextDisplay(notice))
        add_strip(panel, "CHANNEL & DELIVERY")
        panel.add_item(
            discord.ui.TextDisplay(
                "**CHECKLIST CHANNEL**\nChoose an existing checklist to configure its logs."
            )
        )
        panel.add_item(discord.ui.ActionRow(self.boards))
        panel.add_item(
            discord.ui.TextDisplay(
                "**LOG DESTINATION**\nChoose a separate channel, or clear to turn logging off."
            )
        )
        panel.add_item(discord.ui.ActionRow(self.logs))
        if self.back_menu is not None:

            async def back(i):
                await defer_menu(i, self.parent)
                await self.back_menu.return_to(i)
                self.stop()

            button = discord.ui.Button(label="Back to Logs")
            button.callback = back
            panel.add_item(
                discord.ui.Section("**LOGS**\nReturn to log categories.", accessory=button)
            )

        async def home(i):
            self.parent = menu_parent(self.parent, i)
            await defer_menu(i, self.parent)
            if self.parent is not None and getattr(self.parent, "panel_manager", None):
                await self.parent.panel_manager.reset(self.guild)
            elif self.parent is not None:
                self.parent.section = "overview"
                self.parent.back_menu = None
                await self.parent.refresh(i)
            else:
                closed = discord.ui.LayoutView(timeout=1)
                closed.add_item(discord.ui.TextDisplay("Checklist log settings closed."))
                await i.edit_original_response(content=None, view=closed, attachments=[])
                closed.stop()
            self.stop()

        button = discord.ui.Button(label="Home" if self.parent is not None else "Close")
        button.callback = home
        panel.add_item(
            discord.ui.Section(
                "**COMMAND CENTER**\nReturn to the main menu."
                if self.parent is not None
                else "**CHECKLIST LOGS**\nClose these settings.",
                accessory=button,
            )
        )
        panel.add_item(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://checklist_logs_footer.png"))
        )
        section_dividers(panel, navigation_on_right=True)
        self.add_item(panel)

    async def show(self, interaction, notice=None):
        self.parent = menu_parent(self.parent, interaction)
        await defer_menu(interaction, self.parent)
        self.build(notice)
        attachments = [
            discord.File(
                io.BytesIO(files("ky_bot.design").joinpath("assets", folder, asset).read_bytes()),
                filename=name,
            )
            for folder, asset, name in (
                ("header", "ky_header_checklist_logs.png", "checklist_logs_header.png"),
                ("footer", "ky_footer_command_center.png", "checklist_logs_footer.png"),
            )
        ] + category_files(["CHANNEL & DELIVERY"], "silver_neon")
        try:
            await display_menu(interaction, self, attachments, self.parent)
        finally:
            for attachment in attachments:
                attachment.close()

    async def interaction_check(self, interaction):
        self.parent = menu_parent(self.parent, interaction)
        if self.parent is not None and not await self.parent.interaction_check(interaction):
            return False
        if interaction.user.id != self.owner or interaction.guild_id != self.guild:
            await send_private(interaction, "Open your own checklist logging controls.")
            return False
        try:
            await administrator(interaction)
            return True
        except ValueError as error:
            await send_private(interaction, str(error))
            return False

    async def choose_board(self, interaction):
        await administrator(interaction)
        board = self.service.store.board(self.boards.values[0].id)
        if not board or board["guild"] != self.guild:
            raise ValueError("Choose an existing checklist channel in this server.")
        self.channel, self.revision = board["channel"], board["revision"]
        self.boards.default_values = [discord.Object(id=self.channel)]
        self.logs.disabled = False
        self.logs.default_values = (
            [discord.Object(id=board["log_channel"])] if board["log_channel"] else []
        )
        destination = f"<#{board['log_channel']}>" if board["log_channel"] else "Off"
        await self.show(interaction, f"Checklist: <#{self.channel}> · Logging: {destination}")

    async def choose_logs(self, interaction):
        await administrator(interaction)
        channel = self.logs.values[0] if self.logs.values else None
        if channel:
            channel = await self.service.channel(channel.id)
            if channel.guild.id != self.guild or channel.id == self.channel:
                raise ValueError("Choose a separate log channel in this server.")
            self.service.check_permissions(channel, logs=True)
        board = self.service.store.board(self.channel)
        if not board or board["guild"] != self.guild or board["revision"] != self.revision:
            raise ValueError("This checklist changed. Choose it again before saving.")
        self.service.store.update_board(self.channel, log_channel=channel.id if channel else 0)
        self.revision = self.service.store.board(self.channel)["revision"]
        self.logs.default_values = [discord.Object(id=channel.id)] if channel else []
        self.service.wakeup.set()
        await self.show(
            interaction,
            f"Saved for <#{self.channel}>: " + (f"<#{channel.id}>" if channel else "Off"),
        )

    async def on_error(self, interaction, error, item):
        if isinstance(error, ValueError):
            await send_private(interaction, str(error))
        else:
            await report_error(interaction, error)
