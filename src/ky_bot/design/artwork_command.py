"""Remove existing generated artwork posts through a message context menu."""

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from ky_bot.bot import KYBot


class Artwork(commands.Cog):
    def __init__(self, bot: "KYBot") -> None:
        self.bot = bot
        self.delete_menu = app_commands.ContextMenu(
            name="Delete header/footer", callback=self.delete_artwork
        )
        self.bot.tree.add_command(self.delete_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.delete_menu.name, type=self.delete_menu.type)

    @app_commands.guild_only()
    async def delete_artwork(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        if message.flags.ephemeral:
            await interaction.response.send_message(
                "Use Dismiss message below that private reply to remove it.", ephemeral=True
            )
            return
        artwork = message.attachments and all(
            item.filename.lower().startswith(("ky_header_", "ky_footer_"))
            and item.filename.lower().endswith(".png")
            for item in message.attachments
        )
        if not artwork or message.guild is None or message.guild.id != interaction.guild_id:
            await interaction.response.send_message(
                "Choose a message containing only KY BOT header or footer PNG exports.",
                ephemeral=True,
            )
            return
        creator = getattr(message, "interaction_metadata", None)
        creator_id = getattr(getattr(creator, "user", None), "id", None)
        if creator_id is None:
            legacy = getattr(message, "interaction", None)
            creator_id = getattr(getattr(legacy, "user", None), "id", None)
        if message.author.id != self.bot.user.id:
            creator_id = message.author.id
        permissions = message.channel.permissions_for(interaction.user)
        if creator_id != interaction.user.id and not permissions.manage_messages:
            await interaction.response.send_message(
                "You can delete your own artwork posts. Manage Messages permission is needed "
                "to remove someone else's post or an older bot post with no creator recorded.",
                ephemeral=True,
            )
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await message.delete()
        except discord.NotFound:
            result = "That header/footer has already been deleted."
        except discord.Forbidden:
            result = "I need Manage Messages permission in this channel to delete that post."
        else:
            result = "Deleted the header/footer post."
        await interaction.followup.send(result, ephemeral=True)


async def setup(bot: "KYBot") -> None:
    await bot.add_cog(Artwork(bot))
