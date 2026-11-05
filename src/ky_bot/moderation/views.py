"""Iridescent silver moderation menus and private review of privileged actions."""

import asyncio
import io
from importlib.resources import files

import discord

from ky_bot.errors import report_error, send_private
from ky_bot.views.menu_strips import add_strip, category_files, group_sections, strip_files
from ky_bot.views.sections import section_dividers

from .service import duration_label, parse_duration


def safe(text):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(text)))


def artwork(view=None):
    return [
        discord.File(
            io.BytesIO(files("ky_bot.design").joinpath("assets", folder, name).read_bytes()),
            filename=name,
        )
        for folder, name in (
            ("header", "ky_header_moderation.png"),
            ("footer", "ky_footer_moderation.png"),
        )
    ] + (
        category_files(view.category_titles, "silver_neon")
        if view is not None and hasattr(view, "category_titles")
        else strip_files("moderation", "silver_neon")
    )


async def display(interaction, view):
    attachments = artwork(view)
    try:
        return await interaction.edit_original_response(
            content=None,
            embeds=[],
            attachments=attachments,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    finally:
        for attachment in attachments:
            attachment.close()


def button(label, callback, *, danger=False):
    item = discord.ui.Button(
        label=label, style=discord.ButtonStyle.danger if danger else discord.ButtonStyle.secondary
    )
    item.callback = callback
    return item


class Panel(discord.ui.LayoutView):
    def __init__(self, owner, guild):
        super().__init__(timeout=300)
        self.owner, self.guild = owner, guild
        self.panel = discord.ui.Container(accent_colour=0xD6E2F3)
        self.panel.add_item(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem("attachment://ky_header_moderation.png")
            )
        )
        self.add_item(self.panel)

    def text(self, value):
        self.panel.add_item(discord.ui.TextDisplay(value))

    def row(self, value, control):
        self.panel.add_item(discord.ui.Section(value, accessory=control))

    def finish(self):
        if not hasattr(self, "category_titles"):
            group_sections(self.panel, "moderation")
        section_dividers(self.panel)
        self.panel.add_item(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem("attachment://ky_footer_moderation.png")
            )
        )

    async def interaction_check(self, i):
        if i.guild_id == self.guild and i.user.id == self.owner:
            return True
        await send_private(i, "Open your own moderation menu in this server.")
        return False

    async def on_error(self, i, error, item):
        if isinstance(error, ValueError):
            await send_private(i, str(error))
        else:
            await report_error(i, error)


class Menu(Panel):
    def __init__(self, cog, parent):
        super().__init__(parent.owner_id, parent.guild_id)
        self.parent = parent
        self.timeout = None if parent.panel_manager else 300
        self.text(
            "**MEMBER MODERATION**\nChoose an action. Member selection, reasons and "
            "confirmation stay private."
        )
        for action, title, description in (
            ("kick", "KICK A MEMBER", "Remove a member; they can rejoin with an invite."),
            ("ban", "BAN A MEMBER", "Remove a member and prevent them from rejoining."),
            (
                "timeout",
                "TIME OUT A MEMBER",
                "Restrict communication for a custom duration, up to 28 days.",
            ),
        ):

            async def open_form(i, action=action):
                await i.response.defer(ephemeral=True, thinking=True)
                await display(i, MemberForm(cog, i.user.id, i.guild_id, action))

            self.row(f"**{title}**\n-# {description}", button("Choose member", open_form))
        self.text(
            "-# Kick Members, Ban Members or Moderate Members permissions are required. "
            "Both your role "
            "and KY BOT’s role must be above the target. Every action is reviewed first."
        )

        async def home(i):
            await i.response.defer()
            if parent.panel_manager:
                await parent.panel_manager.reset(parent.guild_id)
            else:
                parent.section = "overview"
                await parent.refresh(i)
            self.stop()

        self.row("**COMMAND CENTER**\n-# Return to the main menu.", button("Home", home))
        self.finish()

    async def interaction_check(self, i):
        return await self.parent.interaction_check(i)


class MemberForm(Panel):
    def __init__(self, cog, owner, guild, action):
        super().__init__(owner, guild)
        self.category_titles = ("MEMBER SELECTION",)
        add_strip(self.panel, "MEMBER SELECTION")
        self.cog, self.action, self.target = cog, action, None
        self.text(
            "**"
            + {"ban": "BAN A MEMBER", "kick": "KICK A MEMBER", "timeout": "TIME OUT A MEMBER"}[
                action
            ]
            + "**\nChoose a server member. You’ll review the action before it is applied."
        )
        select = discord.ui.UserSelect(placeholder="Choose a member")

        async def selected(i):
            self.target = select.values[0].id
            await i.response.defer()

        select.callback = selected
        self.panel.add_item(discord.ui.ActionRow(select))

        async def details(i):
            if self.target is None:
                return await send_private(i, "Choose a member first.")
            await i.response.send_modal(Details(cog, owner, guild, self.target, action))

        self.row(
            "**ACTION DETAILS**\n"
            + (
                "Enter a custom duration and optional reason."
                if action == "timeout"
                else "Add an optional reason for the audit log."
            ),
            button("Continue", details),
        )

        async def close(i):
            await i.response.edit_message(
                view=discord.ui.LayoutView().add_item(
                    discord.ui.TextDisplay("Moderation menu closed. No action was taken.")
                ),
                attachments=[],
            )
            self.stop()

        self.row(
            "**PRIVATE CONTROLS**\n-# No action is taken until you confirm.", button("Close", close)
        )
        self.finish()


class Details(discord.ui.Modal):
    def __init__(self, cog, owner, guild, target, action):
        super().__init__(
            title={"ban": "Ban details", "kick": "Kick details", "timeout": "Timeout details"}[
                action
            ],
            timeout=300,
        )
        self.cog, self.owner, self.guild, self.target, self.action = (
            cog,
            owner,
            guild,
            target,
            action,
        )
        self.duration = None
        if action == "timeout":
            self.duration = discord.ui.TextInput(
                label="Duration (up to 28 days)",
                placeholder="30m, 2h, 3d 12h, or 1 hour 30 minutes",
                max_length=100,
            )
            self.add_item(self.duration)
        self.reason = discord.ui.TextInput(
            label="Reason (optional)",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=400,
        )
        self.add_item(self.reason)

    async def on_submit(self, i):
        if i.user.id != self.owner or i.guild_id != self.guild:
            return await send_private(i, "This form belongs to another moderator.")
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            duration = parse_duration(self.duration.value) if self.duration is not None else None
            await self.cog.review(i, self.target, self.action, duration, self.reason.value)
        except ValueError as error:
            await send_private(i, str(error))

    async def on_error(self, i, error):
        await report_error(i, error)


class Confirm(Panel):
    def __init__(self, cog, owner, guild, target, name, action, duration, reason):
        super().__init__(owner, guild)
        self.category_titles = ("REVIEW ACTION",)
        add_strip(self.panel, "REVIEW ACTION")
        self.cog, self.target, self.action = cog, target, action
        self.duration, self.reason = duration, reason
        self.used = False
        self.lock = asyncio.Lock()
        self.text(
            f"**REVIEW {action.upper()}**\n"
            f"**Member:** {safe(name)} · User ID: `{target}`\n"
            + (f"**Duration:** {duration_label(duration)}\n" if duration else "")
            + f"**Reason:** {safe(reason) if reason else 'No reason provided'}"
        )
        self.text(
            "Banning prevents rejoining until unbanned. Existing message history is kept."
            if action == "ban"
            else "Kicking removes the member. They can rejoin with a valid invite. "
            "Existing messages are kept."
            if action == "kick"
            else "The timeout ends automatically. Applying this "
            "replaces any timeout already on this member."
        )
        self.row(
            "**CONFIRM ACTION**\nPermissions and role hierarchy will be checked again.",
            button(
                "Confirm " + action,
                self.confirm,
                danger=action in {"ban", "kick"},
            ),
        )
        self.row("**CANCEL**\nLeave this member unchanged.", button("Cancel", self.cancel))
        self.finish()

    async def confirm(self, i):
        if not await self.interaction_check(i):
            return
        await i.response.defer()
        async with self.lock:
            if self.used:
                return await send_private(i, "This confirmation has already been handled.")
            # A network error can have an uncertain outcome: never automatically repeat an action.
            self.used = True
            try:
                target, until = await self.cog.execute(
                    i, self.target, self.action, self.duration, self.reason
                )
                summary = (
                    f"**MEMBER BANNED**\n{safe(target.display_name)} has been banned."
                    if self.action == "ban"
                    else f"**MEMBER KICKED**\n{safe(target.display_name)} has been kicked."
                    if self.action == "kick"
                    else f"**MEMBER TIMED OUT**\n{safe(target.display_name)} · "
                    f"{duration_label(self.duration)}\nEnds {discord.utils.format_dt(until, 'F')}."
                )
            except ValueError as error:
                summary = "**ACTION NOT APPLIED**\n" + safe(str(error))
            except discord.Forbidden:
                summary = (
                    "**ACTION BLOCKED**\nDiscord denied the action. Check permissions "
                    "and role hierarchy, then open a new confirmation."
                )
            except discord.NotFound:
                summary = "**MEMBER UNAVAILABLE**\nThe member is no longer in this server."
            except discord.HTTPException:
                summary = (
                    "**ACTION COULD NOT BE CONFIRMED**\nCheck the member’s current "
                    "status and Discord’s audit log before trying again."
                )
            result = Panel(self.owner, self.guild)
            result.text(summary)
            result.finish()
            await display(i, result)
            self.stop()

    async def cancel(self, i):
        if not await self.interaction_check(i):
            return
        await i.response.defer()
        async with self.lock:
            if self.used:
                return await send_private(i, "This confirmation has already been handled.")
            self.used = True
            result = Panel(self.owner, self.guild)
            result.text("**ACTION CANCELLED**\nNo moderation action was taken.")
            result.finish()
            await display(i, result)
            self.stop()
