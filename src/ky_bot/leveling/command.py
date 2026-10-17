"""Command Center leveling service; no separate setup commands or public posts on load."""

from discord.ext import commands

from ky_bot.errors import send_private
from ky_bot.leveling.menu import Session
from ky_bot.leveling.service import LevelingService
from ky_bot.leveling.store import LevelStore


class Leveling(commands.Cog):
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

    async def open_menu(self, interaction, *, command_center=None):
        if not interaction.guild or not interaction.permissions.administrator:
            await send_private(interaction, "Open Leveling as a server administrator.")
            return
        await interaction.response.defer(ephemeral=True, thinking=command_center is None)
        session = Session(self, interaction, command_center)
        await session.show(interaction)


async def setup(bot):
    await bot.add_cog(Leveling(bot))
