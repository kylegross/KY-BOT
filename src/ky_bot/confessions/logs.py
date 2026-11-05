"""A single navigation hub for the existing, feature-owned logging settings."""

import base64
from importlib.resources import files

import discord
from discord.ext import commands

from ky_bot.errors import send_private
from ky_bot.views.sections import section_dividers

from .cards import uploads
from .command import administrator
from .store import defaults


class Logs(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def open_menu(self, interaction, *, command_center=None):
        await administrator(interaction)
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True, thinking=True)
        await LogMenu(self, interaction.user.id, interaction.guild_id, command_center).show(
            interaction
        )


class LogMenu(discord.ui.LayoutView):
    def __init__(self, cog, owner, guild, parent):
        super().__init__(timeout=600)
        self.cog, self.owner, self.guild, self.parent = cog, owner, guild, parent

    async def interaction_check(self, i):
        if i.user.id != self.owner or i.guild_id != self.guild:
            await send_private(i, "Open your own Logs menu.")
            return False
        try:
            await administrator(i)
            return True
        except ValueError as error:
            await send_private(i, str(error))
            return False

    async def show(self, i, automation=False):
        self.clear_items()
        config = defaults()
        config["header"] = base64.b64encode(
            files("ky_bot.confessions").joinpath("assets", "logs.png").read_bytes()
        ).decode("ascii")
        panel = discord.ui.Container(accent_colour=int(config["color"], 16))
        panel.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://confession_header.png"))
        )
        panel.add_item(
            discord.ui.TextDisplay(
                "**LOGS**\nManage logging in one place. Feature menus and these "
                "shortcuts share the same saved settings."
            )
        )

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
                await administrator(event)
                cog = self.cog.bot.get_cog(name)
                if not cog:
                    return await send_private(event, "This feature is temporarily unavailable.")
                await cog.open_menu(event, command_center=self.parent, **kwargs)

            return open_feature

        async def server_activity(event):
            await administrator(event)
            await event.response.defer(ephemeral=True)
            self.parent.section = "logging"
            await self.parent.refresh(event)

        async def checklist_logs(event):
            from .checklist_logs import ChecklistLogs

            await administrator(event)
            cog = self.cog.bot.get_cog("Checklists")
            if not cog:
                return await send_private(event, "Checklists are temporarily unavailable.")
            await event.response.send_message(
                "**CHECKLIST LOGS**\nChoose an existing checklist, then its log destination.",
                view=ChecklistLogs(cog.service, event.user.id, event.guild_id),
                ephemeral=True,
            )

        async def automation_menu(event):
            await event.response.defer(ephemeral=True)
            await self.show(event, True)

        if automation:
            panel.add_item(discord.ui.TextDisplay("**AUTOMATION LOGS**"))
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
            panel.add_item(discord.ui.TextDisplay("**SERVER ACTIVITY**"))
            row(
                "SERVER EVENTS & MEMBERS",
                "Messages, members, roles, bans and invites.",
                server_activity,
            )
            panel.add_item(discord.ui.TextDisplay("**OTHER LOGS**"))
            row(
                "CONFESSIONS",
                "Per-channel author logs and administrator review/report destinations.",
                feature("Confessions", logs=True),
            )
            row(
                "ASK TO DM/FR",
                "Request outcomes and private staff-log destination.",
                feature("AskDM"),
            )
            row("CHECKLISTS", "Completion logs for each existing checklist.", checklist_logs)
            row(
                "AUTOMATION", "Autodelete, autothread and autoreact activity logs.", automation_menu
            )

        async def home(event):
            await event.response.defer(ephemeral=True)
            self.parent.section = "overview"
            await self.parent.refresh(event)

        async def back(event):
            await event.response.defer(ephemeral=True)
            await self.show(event)

        panel.add_item(
            discord.ui.ActionRow(button("Back", back if automation else home), button("Home", home))
        )
        panel.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://confession_footer.png"))
        )
        section_dividers(panel)
        self.add_item(panel)
        attachments = uploads(config)
        try:
            await i.edit_original_response(
                view=self,
                attachments=attachments,
                content=None,
                embeds=[],
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            for file in attachments:
                file.close()
