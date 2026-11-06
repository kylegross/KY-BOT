"""A single navigation hub for the existing, feature-owned logging settings."""

import base64
from importlib.resources import files

import discord
from discord.ext import commands

from ky_bot.errors import send_private
from ky_bot.views.menu_context import defer_menu, display_menu, menu_parent
from ky_bot.views.menu_strips import add_strip, filename
from ky_bot.views.sections import section_dividers

from .cards import uploads
from .command import administrator
from .store import defaults


class Logs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def open_menu(self, interaction, *, command_center=None):
        command_center = menu_parent(command_center, interaction)
        await administrator(interaction)
        if command_center is not None and hasattr(command_center, "interaction_check"):
            if not await command_center.interaction_check(interaction):
                return
        await defer_menu(interaction, command_center)
        await LogMenu(self, interaction.user.id, interaction.guild_id, command_center).show(
            interaction
        )


class LogMenu(discord.ui.LayoutView):
    def __init__(self, cog, owner, guild, parent):
        super().__init__(timeout=600)
        self.cog, self.owner, self.guild, self.parent = cog, owner, guild, parent

    async def interaction_check(self, i):
        self.parent = menu_parent(self.parent, i)
        if self.parent is not None and hasattr(self.parent, "interaction_check"):
            if not await self.parent.interaction_check(i):
                return False
        if i.user.id != self.owner or i.guild_id != self.guild:
            await send_private(i, "Open your own Logs menu.")
            return False
        try:
            await administrator(i)
            return True
        except ValueError as error:
            await send_private(i, str(error))
            return False

    async def return_to(self, i):
        replacement = LogMenu(self.cog, self.owner, self.guild, menu_parent(self.parent, i))
        await replacement.show(i, getattr(self, "automation", False))

    async def show(self, i, automation=False):
        self.parent = menu_parent(self.parent, i)
        self.automation = automation
        self.clear_items()
        config = defaults()
        config["header"] = base64.b64encode(
            files("ky_bot.confessions").joinpath("assets", "logs.png").read_bytes()
        ).decode("ascii")
        panel = discord.ui.Container(accent_colour=int(config["color"], 16))
        panel.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://confession_header.png"))
        )
        panel.add_item(discord.ui.TextDisplay("Manage destinations and delivery for all logs."))

        def button(label, callback):
            item = discord.ui.Button(label=label)
            item.callback = callback
            return item

        def row(title, text, callback):
            panel.add_item(
                discord.ui.Section(
                    "**" + title + "**\n-# " + text, accessory=button("Manage", callback)
                )
            )

        def feature(name, **kwargs):
            async def open_feature(event):
                self.parent = menu_parent(self.parent, event)
                await administrator(event)
                cog = self.cog.bot.get_cog(name)
                if not cog:
                    return await send_private(event, "This feature is temporarily unavailable.")
                await cog.open_menu(event, command_center=self.parent, **kwargs)

            return open_feature

        async def server_activity(event):
            await administrator(event)
            await defer_menu(event, self.parent)
            self.parent = menu_parent(self.parent, event)
            self.parent.section = "logging"
            self.parent.back_menu = self
            await self.parent.refresh(event)
            self.stop()

        async def checklist_logs(event):
            from .checklist_logs import ChecklistLogs

            await administrator(event)
            cog = self.cog.bot.get_cog("Checklists")
            if not cog:
                return await send_private(event, "Checklists are temporarily unavailable.")
            self.parent = menu_parent(self.parent, event)
            view = ChecklistLogs(
                cog.service, event.user.id, event.guild_id, parent=self.parent, back_menu=self
            )
            await view.show(event)
            self.stop()

        async def automation_menu(event):
            await defer_menu(event, self.parent)
            await self.show(event, True)

        if automation:
            add_strip(panel, "ACTIVITY LOGS")
            for title, name in (
                ("AUTODELETE", "AutoDelete"),
                ("AUTOTHREAD", "AutoThread"),
                ("AUTOREACT", "AutoReact"),
            ):
                row(
                    title,
                    "Open the feature’s activity-log destination and delivery settings.",
                    feature(name),
                )
        else:
            add_strip(panel, "SERVER ACTIVITY")
            row(
                "SERVER EVENTS & MEMBERS",
                "Messages, members, roles, bans and invites.",
                server_activity,
            )
            add_strip(panel, "OTHER LOGS")
            row(
                "NSFW FLASH LOGS",
                "Age restriction, optional spoilers and private text-only channel logs.",
                feature("NSFWFlashLogs"),
            )
            row(
                "CONFESSIONS",
                "Author logs, approvals and reports.",
                feature("Confessions", logs=True),
            )
            row(
                "ASK TO DM/FR",
                "Request outcomes and private staff logs.",
                feature("AskDM"),
            )
            row("CHECKLISTS", "Completion logs for each existing checklist.", checklist_logs)
            row(
                "AUTOMATION", "Autodelete, autothread and autoreact activity logs.", automation_menu
            )

        async def home(event):
            self.parent = menu_parent(self.parent, event)
            await defer_menu(event, self.parent)
            if self.parent is not None and getattr(self.parent, "panel_manager", None):
                await self.parent.panel_manager.reset(self.guild)
            elif self.parent is not None and hasattr(self.parent, "refresh"):
                self.parent.section = "overview"
                self.parent.back_menu = None
                await self.parent.refresh(event)
            else:
                closed = discord.ui.LayoutView(timeout=1)
                closed.add_item(discord.ui.TextDisplay("Logs menu closed."))
                await event.edit_original_response(content=None, view=closed, attachments=[])
                closed.stop()
            self.stop()

        async def back(event):
            await defer_menu(event, self.parent)
            await self.show(event)

        navigation = ([button("Back", back)] if automation else []) + [
            button("Home" if self.parent is not None else "Close", home)
        ]
        panel.add_item(discord.ui.ActionRow(*navigation))
        panel.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://confession_footer.png"))
        )
        section_dividers(panel)
        self.add_item(panel)
        attachments = uploads(config)
        import io

        attachments.extend(
            discord.File(
                io.BytesIO(
                    files("ky_bot.design")
                    .joinpath("assets", "strips", "silver_neon_" + filename(title))
                    .read_bytes()
                ),
                filename=filename(title),
            )
            for title in (("ACTIVITY LOGS",) if automation else ("SERVER ACTIVITY", "OTHER LOGS"))
        )
        try:
            await display_menu(i, self, attachments, self.parent)
        finally:
            for file in attachments:
                file.close()
