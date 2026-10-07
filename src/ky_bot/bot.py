"""Application lifecycle and explicit extension registry."""

import logging
from contextlib import AsyncExitStack

import discord
from discord.ext import commands

from ky_bot.config import Settings
from ky_bot.database.settings import open_settings
from ky_bot.errors import CommandTree
from ky_bot.services.settings import SettingsService

log = logging.getLogger(__name__)
EXTENSIONS = (
    "ky_bot.cogs.core",
    "ky_bot.cogs.settings",
    "ky_bot.cogs.footers",
    "ky_bot.cogs.headers",
    "ky_bot.cogs.artwork",
    "ky_bot.cogs.activity",
)


def build_intents() -> discord.Intents:
    intents = discord.Intents.none()
    intents.guilds = True
    intents.members = True
    intents.messages = True
    intents.message_content = True
    intents.moderation = True
    intents.invites = True
    intents.presences = False
    return intents


class KYBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned,
            help_command=None,
            tree_cls=CommandTree,
            intents=build_intents(),
            allowed_mentions=discord.AllowedMentions.none(),
            chunk_guilds_at_startup=True,
            member_cache_flags=discord.MemberCacheFlags.from_intents(build_intents()),
            activity=discord.Game(name="/help · KY BOT"),
        )
        self.settings = settings
        # Async services share the bot lifecycle and are released by close().
        self.resources = AsyncExitStack()
        self.server_settings: SettingsService

    async def setup_hook(self) -> None:
        repository = await self.resources.enter_async_context(
            open_settings(self.settings.database_path)
        )
        self.server_settings = SettingsService(repository)
        for extension in EXTENSIONS:
            await self.load_extension(extension)
            log.info("Loaded extension %s", extension)
        mode = self.settings.command_sync
        if mode == "guild":
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            log.info("Synced %s commands to test server %s", len(synced), guild.id)
        elif mode == "global":
            synced = await self.tree.sync()
            log.info("Synced %s global commands", len(synced))
        else:
            log.info("Command sync skipped (COMMAND_SYNC=none)")

    async def on_ready(self) -> None:
        log.info(
            "KY BOT online as %s (ID %s), in %s servers", self.user, self.user.id, len(self.guilds)
        )

    async def on_message(self, message: discord.Message) -> None:
        # Slash-only foundation: do not process text prefixes. Cog listeners still receive events.
        pass

    async def on_error(self, event_method: str, *args: object, **kwargs: object) -> None:
        log.exception("Unhandled event error in %s", event_method)

    async def close(self) -> None:
        try:
            await super().close()
        finally:
            await self.resources.aclose()
