"""Slash commands and Command Center access share the same reviewed actions."""

import discord
from discord import app_commands
from discord.ext import commands

from ky_bot.errors import send_private

from .service import apply, authorize, parse_duration
from .views import Confirm, Menu, artwork, display


class Moderation(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def review(self, i, target_id, action, duration=None, reason=""):
        if action == "timeout" and duration is None:
            raise ValueError("Enter a custom timeout duration, for example 2h 30m.")
        _, target = await authorize(i.guild, i.user.id, target_id, self.bot.user.id, action)
        reason = (reason or "").strip()
        if len(reason) > 400:
            raise ValueError("Keep the reason to 400 characters or fewer.")
        await display(
            i,
            Confirm(
                self,
                i.user.id,
                i.guild_id,
                target_id,
                target.display_name,
                action,
                duration,
                reason,
            ),
        )

    async def execute(self, i, target_id, action, duration, reason):
        return await apply(
            i.guild,
            i.user.id,
            target_id,
            self.bot.user.id,
            action,
            duration=duration,
            reason=reason,
        )

    @app_commands.command(name="ban", description="Review and ban a server member.")
    @app_commands.guild_only()
    @app_commands.default_permissions(ban_members=True)
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.describe(member="Server member to ban", reason="Optional audit-log reason")
    async def ban(
        self,
        i: discord.Interaction,
        member: discord.Member,
        reason: app_commands.Range[str, 1, 400] | None = None,
    ):
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            await self.review(i, member.id, "ban", reason=reason)
        except ValueError as error:
            await send_private(i, str(error))

    @app_commands.command(
        name="timeout", description="Review a custom timeout for a server member."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.describe(
        member="Server member to time out",
        duration="Custom time: 30m, 2h, 3d 12h or 1 hour 30 minutes (maximum 28 days)",
        reason="Optional audit-log reason",
    )
    async def timeout(
        self,
        i: discord.Interaction,
        member: discord.Member,
        duration: app_commands.Range[str, 1, 100],
        reason: app_commands.Range[str, 1, 400] | None = None,
    ):
        await i.response.defer(ephemeral=True, thinking=True)
        try:
            await self.review(i, member.id, "timeout", parse_duration(duration), reason)
        except ValueError as error:
            await send_private(i, str(error))

    async def open_menu(self, i, *, command_center=None):
        if command_center is None or not await command_center.interaction_check(i):
            return
        await i.response.defer()
        view = Menu(self, command_center)
        attachments = artwork()
        try:
            if command_center.panel_manager:
                await command_center.panel_manager.edit(command_center, i, view, attachments)
            else:
                await i.edit_original_response(
                    content=None,
                    embeds=[],
                    view=view,
                    attachments=attachments,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        finally:
            for attachment in attachments:
                attachment.close()


async def setup(bot):
    await bot.add_cog(Moderation(bot))
