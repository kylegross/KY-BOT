"""Command Center leveling service; no separate setup commands or public posts on load."""

import discord
from discord import app_commands
from discord.ext import commands

from ky_bot.errors import send_private
from ky_bot.leveling.menu import Session
from ky_bot.leveling.service import LevelingService
from ky_bot.leveling.store import MAX_LEVEL, MAX_XP, LevelStore


class Leveling(commands.Cog):
    xp = app_commands.Group(
        name="xp",
        description="Adjust a server member's XP or level.",
        guild_only=True,
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.store = await LevelStore.open(
            self.bot.settings.database_path, self.bot.settings.database_url
        )
        self.service = LevelingService(self.bot, self.store)
        self.service.start()

    async def cog_unload(self):
        await self.service.close()

    @commands.Cog.listener()
    async def on_message(self, message):
        await self.service.on_message(message)

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        self.service.voice_changed(member, before, after)

    @commands.Cog.listener()
    async def on_disconnect(self):
        self.service.voice.clear()

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        if before.roles != after.roles:
            self.service.voice_changed(after, before.voice, after.voice)

    async def adjust_command(self, interaction, member, action, value):
        if not interaction.guild or not interaction.permissions.administrator:
            await send_private(interaction, "Only server administrators can adjust member XP.")
            return
        if member.guild.id != interaction.guild_id:
            await send_private(interaction, "Choose a member of this server.")
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await self.service.adjust_member(
                interaction.guild,
                member.id,
                action,
                value,
                actor=interaction.user.id,
                operation=interaction.id,
            )
        except ValueError as error:
            await send_private(interaction, str(error))
            return
        await interaction.edit_original_response(
            content=f"<@{member.id}>: **{result['before_xp']:,} → {result['xp']:,} XP** · "
            f"**Level {result['level']}**.\nExisting roles are kept.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @xp.command(name="add", description="Add XP to a member in this server.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(member="Member to award XP to", amount="XP to add")
    async def xp_add(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, 1, MAX_XP],
    ):
        await self.adjust_command(interaction, member, "add", amount)

    @xp.command(name="remove", description="Remove XP from a member, down to zero.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(member="Member to remove XP from", amount="XP to remove")
    async def xp_remove(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        amount: app_commands.Range[int, 1, MAX_XP],
    ):
        await self.adjust_command(interaction, member, "remove", amount)

    @xp.command(name="level", description="Set a member's XP to the start of a specific level.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(member="Member to update", level="Exact level to set (0–10000)")
    async def xp_level(
        self,
        interaction: discord.Interaction,
        member: discord.Member,
        level: app_commands.Range[int, 0, MAX_LEVEL],
    ):
        await self.adjust_command(interaction, member, "level", level)

    async def open_menu(self, interaction, *, command_center=None):
        if not interaction.guild or not interaction.permissions.administrator:
            await send_private(interaction, "Open Leveling as a server administrator.")
            return
        await interaction.response.defer(ephemeral=True, thinking=command_center is None)
        session = Session(self, interaction, command_center)
        await session.show(interaction)


async def setup(bot):
    await bot.add_cog(Leveling(bot))
