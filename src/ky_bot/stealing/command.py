"""Slash commands, personal destination and message-context asset selection."""

import asyncio
import io
import json
from collections import defaultdict

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from ky_bot.errors import send_private

from .assets import MESSAGE, Asset, cdn_id, emojis, from_message, name_for, sticker

INSTALLS = app_commands.AppInstallationType(guild=True, user=True)
CONTEXTS = app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True)


def can_create(member):
    permissions = member.guild_permissions
    return (
        permissions.administrator
        or permissions.create_expressions
        or permissions.manage_expressions
    )


def ephemeral(interaction):
    return getattr(getattr(interaction, "context", None), "value", 0) != 1


class DestinationError(ValueError):
    pass


class Steal(commands.Cog):
    steal = app_commands.Group(
        name="steal",
        description="Copy custom emojis and stickers to a server.",
        allowed_installs=INSTALLS,
        allowed_contexts=CONTEXTS,
    )

    def __init__(self, bot):
        self.bot = bot
        self.upload_locks = defaultdict(asyncio.Lock)
        self.menu = app_commands.ContextMenu(
            name="Steal emojis / stickers",
            callback=self.message_menu,
            allowed_installs=INSTALLS,
            allowed_contexts=CONTEXTS,
        )

    async def cog_load(self):
        self.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        self.bot.tree.add_command(self.menu)

    async def cog_unload(self):
        self.bot.tree.remove_command(self.menu.name, type=self.menu.type)
        await self.http.close()

    @property
    def repository(self):
        return self.bot.server_settings.repository

    async def authorize(self, user_id, guild_id):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise DestinationError("Choose a destination server with KY BOT installed.")
        try:
            member = await guild.fetch_member(user_id)
            bot = await guild.fetch_member(self.bot.user.id)
        except discord.NotFound:
            raise DestinationError(
                "You and KY BOT must both belong to the destination server."
            ) from None
        if not can_create(member):
            raise DestinationError(
                "You need Create Expressions or Manage Expressions in the destination."
            )
        if not can_create(bot):
            raise DestinationError("KY BOT needs Create Expressions in the destination server.")
        return guild

    async def destination(self, interaction):
        saved = await self.repository.get_steal_destination(interaction.user.id)
        ident = saved or interaction.guild_id
        if ident is None:
            raise DestinationError("Run /steal set first and choose a destination server.")
        return await self.authorize(interaction.user.id, ident)

    async def resolve_server(self, value):
        value = value.strip()
        if value.isascii() and value.isdigit():
            return int(value)
        matches = [g.id for g in self.bot.guilds if g.name.casefold() == value.casefold()]
        if len(matches) != 1:
            raise ValueError("Choose a server from autocomplete, or paste its server ID.")
        return matches[0]

    @steal.command(
        name="set", description="Save your destination server for copied emojis and stickers."
    )
    @app_commands.describe(
        server="Destination server with KY BOT installed; select it or paste its ID"
    )
    async def set_destination(self, interaction: discord.Interaction, server: str):
        await interaction.response.defer(ephemeral=ephemeral(interaction), thinking=True)
        try:
            guild = await self.authorize(interaction.user.id, await self.resolve_server(server))
            await self.repository.set_steal_destination(interaction.user.id, guild.id)
            await interaction.edit_original_response(
                content=f"Saved **{discord.utils.escape_mentions(guild.name)}** "
                "as your destination. Your emoji and sticker copies will go there, even when "
                "you use KY BOT from another server or a personal installation.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (ValueError, discord.Forbidden) as error:
            await send_private(interaction, str(error))

    @set_destination.autocomplete("server")
    async def server_choices(self, interaction: discord.Interaction, current: str):
        result = []
        for guild in self.bot.guilds:
            member = guild.get_member(interaction.user.id)
            if (
                member
                and guild.me
                and can_create(member)
                and can_create(guild.me)
                and (current.casefold() in guild.name.casefold() or current in str(guild.id))
            ):
                result.append(app_commands.Choice(name=guild.name[:100], value=str(guild.id)))
        return result[:25]

    async def source_message(self, interaction, value):
        match = MESSAGE.fullmatch(value.strip())
        if not match:
            return None
        if match["guild"] == "@me":
            raise ValueError(
                "For a private message, use its Apps → Steal emojis / stickers action."
            )
        guild = self.bot.get_guild(int(match["guild"]))
        if guild is None:
            raise ValueError(
                "KY BOT cannot read that server’s history. On the original message, "
                "use Apps → Steal emojis / stickers with your personal install, "
                "or paste the emoji or sticker ID into the slash command."
            )
        try:
            member = await guild.fetch_member(interaction.user.id)
        except discord.NotFound:
            raise ValueError("You must belong to the source server to open that message.") from None
        channel = guild.get_channel_or_thread(int(match["channel"]))
        if channel is None:
            channel = await self.bot.fetch_channel(int(match["channel"]))
        if not getattr(channel, "guild", None) or channel.guild.id != guild.id:
            raise ValueError("The source message does not belong to that server.")
        bot_member = guild.me or await guild.fetch_member(self.bot.user.id)
        for actor in (member, bot_member):
            permissions = channel.permissions_for(actor)
            if not (permissions.view_channel and permissions.read_message_history):
                raise ValueError("You and KY BOT must be able to view and read the source channel.")
        if isinstance(channel, discord.Thread) and channel.is_private():
            # Parent View Channel alone does not grant membership in a private thread.
            if not member.guild_permissions.manage_threads:
                try:
                    await channel.fetch_member(member.id)
                except discord.NotFound:
                    raise ValueError("You must belong to that private source thread.") from None
        return await channel.fetch_message(int(match["message"]))

    async def assets(self, interaction, source, kind):
        message = await self.source_message(interaction, source)
        if message is not None:
            return [asset for asset in from_message(message) if asset.kind == kind]
        if kind == "emoji":
            parsed = emojis(source)
            if parsed:
                return parsed
            ident = cdn_id(source, kind)
            if ident:
                known = self.bot.get_emoji(ident)
                return [
                    Asset(
                        "emoji",
                        ident,
                        getattr(known, "name", None),
                        getattr(known, "animated", False),
                    )
                ]
            raise ValueError(
                "Paste one or more custom emojis, an emoji ID, or a message link. "
                "Standard Unicode emojis do not need to be copied."
            )
        ident = cdn_id(source, kind)
        if ident is None:
            raise ValueError("Paste a sticker ID, a Discord sticker link, or a message link.")
        return [sticker(await self.bot.fetch_sticker(ident))]

    async def start(self, interaction, assets, name=None):
        from .views import Batch, show

        if not assets:
            raise ValueError("No custom emojis or stickers were found in that source.")
        if name is not None and len(assets) != 1:
            raise ValueError(
                "For multiple assets, leave name blank and use Rename one in the picker."
            )
        guild = await self.destination(interaction)
        view = Batch(
            self,
            interaction.user.id,
            interaction.guild_id,
            interaction.channel_id,
            guild.id,
            guild.name,
            assets,
        )
        if name is not None:
            view.names[assets[0].key] = name_for(assets[0], name)
            view.build()
        await show(interaction, view, attachments=True)

    @steal.command(
        name="emoji", description="Pick custom emojis from text or a message and copy them."
    )
    @app_commands.describe(
        source="Paste custom emojis, an emoji ID/link, or a message link",
        name="Optional name for a single emoji; otherwise keep its original name",
    )
    async def emoji(
        self,
        interaction: discord.Interaction,
        source: app_commands.Range[str, 1, 4000],
        name: app_commands.Range[str, 2, 32] | None = None,
    ):
        await interaction.response.defer(ephemeral=ephemeral(interaction), thinking=True)
        try:
            await self.start(interaction, await self.assets(interaction, source, "emoji"), name)
        except (ValueError, discord.NotFound, discord.Forbidden) as error:
            await send_private(interaction, str(error))

    @steal.command(
        name="sticker", description="Pick posted stickers and copy them to your destination."
    )
    @app_commands.describe(
        source="Sticker ID, Discord sticker link, or a link to the posted message",
        name="Optional custom sticker name; blank keeps the original name",
    )
    async def sticker(
        self,
        interaction: discord.Interaction,
        source: app_commands.Range[str, 1, 4000],
        name: app_commands.Range[str, 2, 30] | None = None,
    ):
        await interaction.response.defer(ephemeral=ephemeral(interaction), thinking=True)
        try:
            await self.start(interaction, await self.assets(interaction, source, "sticker"), name)
        except (ValueError, discord.NotFound, discord.Forbidden) as error:
            await send_private(interaction, str(error))

    async def message_menu(self, interaction: discord.Interaction, message: discord.Message):
        # Discord supplies this specific message to the user-installed context action.
        # Do not attempt to fetch unrelated history in a server where the bot isn't installed.
        await interaction.response.defer(ephemeral=ephemeral(interaction), thinking=True)
        try:
            await self.start(interaction, from_message(message))
        except ValueError as error:
            await send_private(interaction, str(error))

    async def download(self, asset):
        async with self.http.get(asset.url, allow_redirects=False) as response:
            if response.status != 200:
                raise ValueError("The original asset is unavailable from Discord’s CDN.")
            if response.content_length and response.content_length > 2 * 1024 * 1024:
                raise ValueError("This asset exceeds the supported download size.")
            data = bytearray()
            async for chunk in response.content.iter_chunked(65536):
                data.extend(chunk)
                if len(data) > 2 * 1024 * 1024:
                    raise ValueError("This asset exceeds the supported download size.")
        return bytes(data)

    async def copy(self, owner, guild_id, asset, override=None):
        name = name_for(asset, override)
        async with self.upload_locks[guild_id]:
            guild = await self.authorize(owner, guild_id)
            data = await self.download(asset)
            limit = 256 * 1024 if asset.kind == "emoji" else 512 * 1024
            if not data or len(data) > limit:
                raise ValueError(f"Discord limits this {asset.kind} upload to {limit // 1024} KiB.")
            converted = False
            if asset.kind == "sticker" and asset.format == 3:
                if not {"VERIFIED", "PARTNERED"} & set(guild.features):
                    from .animation import convert_lottie

                    data = await asyncio.to_thread(convert_lottie, data)
                    converted = True
            # Recheck after downloading; another member may have changed permissions meanwhile.
            guild = await self.authorize(owner, guild_id)
            saved = await self.repository.get_steal_destination(owner)
            if saved is not None and saved != guild_id:
                raise DestinationError(
                    "Your saved destination changed. Start a new copy operation."
                )
            audit = f"KY BOT | /steal | Requester {owner} | Source {asset.key}"
            if asset.kind == "emoji":
                result = await asyncio.wait_for(
                    guild.create_custom_emoji(name=name, image=data, reason=audit), timeout=45
                )
            else:
                if asset.format == 3 and not converted:
                    try:
                        data = json.dumps(json.loads(data), separators=(",", ":")).encode()
                    except (ValueError, UnicodeDecodeError):
                        raise ValueError("Discord returned an invalid Lottie sticker.") from None
                extension = (
                    "png"
                    if converted
                    else {1: "png", 2: "png", 3: "json", 4: "gif"}.get(asset.format)
                )
                if extension is None:
                    raise ValueError(
                        "That sticker format is not supported by Discord’s upload API."
                    )
                file = discord.File(io.BytesIO(data), filename=f"sticker.{extension}")
                try:
                    result = await asyncio.wait_for(
                        guild.create_sticker(
                            name=name,
                            description=asset.description[:100]
                            if len(asset.description) != 1
                            else "",
                            emoji=asset.tags[:200] or "🙂",
                            file=file,
                            reason=audit,
                        ),
                        timeout=45,
                    )
                finally:
                    file.close()
            return result.id, name


async def setup(bot):
    await bot.add_cog(Steal(bot))
