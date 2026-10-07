import io
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ky_bot.views.settings import SettingsView, settings_files

if TYPE_CHECKING:
    from ky_bot.bot import KYBot


class ServerSettings(commands.Cog):
    def __init__(self, bot: "KYBot") -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        settings = await self.bot.server_settings.repository.get(member.guild.id)
        if settings.welcome_channel_id is None:
            return
        try:
            channel = await self.bot.server_settings.validate_channel(
                member.guild, settings.welcome_channel_id
            )
            # Replace only supported tokens; braces in custom prose remain literal.
            import re

            values = {"member": member.mention, "server": member.guild.name}
            content = re.sub(
                r"\{(member|server)\}", lambda match: values[match[1]], settings.welcome_message
            )
            image = None
            if channel.permissions_for(member.guild.me).attach_files:
                try:
                    image = await self.bot.server_settings.render_welcome_card(member)
                except (ValueError, discord.HTTPException):
                    logging.getLogger(__name__).warning(
                        "Welcome image unavailable in server %s; sending greeting text",
                        member.guild.id,
                    )
            file_options = (
                {
                    "file": discord.File(
                        io.BytesIO(image), filename="welcome.png", description=content[:1024]
                    )
                }
                if image
                else {}
            )
            await channel.send(
                content[:2000],
                allowed_mentions=discord.AllowedMentions(
                    everyone=False, roles=False, users=[member], replied_user=False
                ),
                **file_options,
            )
        except (ValueError, discord.HTTPException):
            logging.getLogger(__name__).warning(
                "Could not send welcome in server %s channel %s",
                member.guild.id,
                settings.welcome_channel_id,
            )

    @app_commands.command(name="settings", description="Configure KY BOT for this server.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def settings_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        settings = await self.bot.server_settings.repository.get(interaction.guild_id)
        view = SettingsView(interaction.user.id, interaction.guild_id, self.bot.server_settings)
        view.settings = settings
        view.guild = interaction.guild
        view.show_section("overview")
        attachments = settings_files("overview")
        try:
            view.message = await interaction.edit_original_response(
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
                attachments=attachments,
            )
        finally:
            for attachment in attachments:
                attachment.close()


async def setup(bot: "KYBot") -> None:
    await bot.add_cog(ServerSettings(bot))
