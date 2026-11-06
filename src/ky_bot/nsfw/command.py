"""Independent age-restriction switches and opt-in VICE-style flash logging."""

import asyncio
import io
import json
from importlib.resources import files

import discord
from discord import app_commands
from discord.ext import commands

from ky_bot.admin_functions.autodelete.autodelete_logging import TYPES
from ky_bot.errors import report_error, send_private
from ky_bot.views.menu_context import defer_menu, display_menu, menu_parent
from ky_bot.views.menu_strips import group_sections, strip_files
from ky_bot.views.sections import section_dividers

PRIVACY = (
    "**WHAT IS LOGGED**\nSelected text/captions, edit before/after text, manual deletions, "
    "reaction removals/clears, member IDs, channel and timestamps. Uploads are notices only. "
    "Optional Tenor/Giphy page links are text-only, with no previews.\n\n"
    "**HOW IT WORKS**\nActivity from the same member/channel is grouped for one minute and "
    "updates the same staff-log embed. Linked replies use a conversation summary. "
    "Long summaries continue in another embed. KY BOT AutoDelete removals are suppressed; "
    "Discord does not identify who manually deleted a message. "
    "The five-minute cleanup timing filter can also omit a manual deletion "
    "in the same 300–330 second window.\n\n"
    "**IMAGES AND VIDEOS ARE NOT SAVED.**\nNo image/video files, filenames, direct media URLs "
    "or previews are copied to these logs. Logging starts after you enable it; it does not "
    "scan old channel history. Posted logs remain until staff delete them."
)


def uploads():
    return [
        discord.File(
            io.BytesIO(files("ky_bot.design").joinpath("assets", directory, name).read_bytes()),
            filename=name,
        )
        for directory, name in (
            ("header", "ky_header_nsfw_flash.png"),
            ("footer", "ky_footer_nsfw_flash.png"),
        )
    ] + strip_files("nsfw", "silver_neon")


class NSFWFlashLogs(commands.Cog):
    nsfw = app_commands.Group(
        name="nsfw", description="Manage channel age restriction and flash logs.", guild_only=True
    )

    def __init__(self, bot):
        self.bot = bot
        self.locks = {}

    @property
    def logs(self):
        cog = self.bot.get_cog("AutoDelete")
        if cog is None:
            raise ValueError("Channel logging is temporarily unavailable.")
        return cog.service.logs

    async def authorize(self, i):
        if i.guild is None:
            raise ValueError("Use NSFW Flash Logs inside a server.")
        member = await i.guild.fetch_member(i.user.id)
        if not member.guild_permissions.administrator:
            raise ValueError(
                "Only server administrators can manage age restriction and flash logs."
            )

    async def channel(self, guild_id, channel_id):
        channel = await self.bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel) or channel.guild.id != guild_id:
            raise ValueError("Choose a text channel in this server.")
        return channel

    async def destination(self, guild_id, channel_id, source):
        channel = await self.channel(guild_id, channel_id)
        if channel.id == source:
            raise ValueError("Choose a log channel separate from the source channel.")
        if not channel.is_nsfw():
            raise ValueError(
                "The staff log channel must also have Discord's Age-Restricted setting enabled."
            )
        permissions = channel.permissions_for(channel.guild.me)
        if not all(
            getattr(permissions, k)
            for k in (
                "view_channel",
                "send_messages",
                "embed_links",
                "attach_files",
                "read_message_history",
            )
        ):
            raise ValueError(
                "KY BOT needs View Channel, Send Messages, Embed Links, Attach Files and "
                "Read Message History in the log channel."
            )
        return channel

    async def apply(
        self,
        i,
        source_id,
        *,
        adult=None,
        logging=None,
        destination=None,
        types=None,
        expected=None,
        require_spoilers=None,
    ):
        await self.authorize(i)
        lock = self.locks.setdefault((i.guild_id, source_id), asyncio.Lock())
        async with lock:
            source = await self.channel(i.guild_id, source_id)
            old = self.logs.flash_config(i.guild_id, source.id)
            if expected is not None and json.dumps(old, sort_keys=True) != expected:
                raise ValueError("These settings changed. Reopen the menu before saving.")
            desired = source.is_nsfw() if adult is None else adult
            if (
                desired
                and (
                    self.logs.spoiler_required(i.guild_id, source.id)
                    if require_spoilers is None
                    else require_spoilers
                )
                and not source.permissions_for(source.guild.me).manage_messages
            ):
                raise ValueError("KY BOT needs Manage Messages to require spoilered media.")
            log_enabled = bool(old and old["enabled"]) if logging is None else logging
            if not desired:
                log_enabled = False
            if log_enabled:
                permissions = source.permissions_for(source.guild.me)
                if not (permissions.view_channel and permissions.read_message_history):
                    raise ValueError(
                        "KY BOT needs View Channel and Read Message History in the source channel."
                    )
            dest = destination if destination is not None else old["destination"] if old else 0
            choices = (
                types if types is not None else json.loads(old["types"]) if old else list(TYPES)
            )
            if not choices or not set(choices) <= TYPES.keys():
                raise ValueError("Choose at least one supported event type.")
            if dest and (log_enabled or destination is not None):
                await self.destination(i.guild_id, dest, source.id)
            elif log_enabled:
                raise ValueError("Choose an age-restricted staff log channel before enabling logs.")
            if desired != source.is_nsfw():
                if not source.permissions_for(source.guild.me).manage_channels:
                    raise ValueError(
                        "KY BOT needs Manage Channels to change Age-Restricted Channel."
                    )
                await source.edit(nsfw=desired, reason=f"KY BOT /nsfw toggle by {i.user.id}")
            self.logs.save_flash(i.guild_id, source.id, dest, choices, log_enabled)
            if require_spoilers is not None:
                self.logs.set_spoilers(i.guild_id, source.id, require_spoilers)
            return desired, log_enabled

    @nsfw.command(
        name="toggle",
        description="Toggle channel age restriction and optionally configure flash logs.",
    )
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.describe(
        channel="Text channel; defaults to the channel where you run this command",
        enabled="Set age restriction explicitly; leave blank to toggle it",
        logs="Enable or disable text-only flash logging for this channel",
        log_channel="Separate age-restricted staff channel for the flash logs",
        require_spoilers="Require spoiler tags; remove unspoilered media posts and ask for reposts",
    )
    async def toggle(
        self,
        i: discord.Interaction,
        channel: discord.TextChannel | None = None,
        enabled: bool | None = None,
        logs: bool | None = None,
        log_channel: discord.TextChannel | None = None,
        require_spoilers: bool | None = None,
    ):
        await i.response.defer(ephemeral=True, thinking=True)
        source = channel or i.channel
        if not isinstance(source, discord.TextChannel):
            return await send_private(i, "Choose a text channel with the channel option.")
        try:
            current = await self.channel(i.guild_id, source.id)
            adult = not current.is_nsfw() if enabled is None else enabled
            result = await self.apply(
                i,
                source.id,
                adult=adult,
                logging=logs,
                destination=log_channel.id if log_channel else None,
                require_spoilers=require_spoilers,
            )
            await self.show(
                i,
                source.id,
                notice=(
                    f"Age restriction {'enabled' if result[0] else 'disabled'} · "
                    f"Flash logs {'enabled' if result[1] else 'disabled'}."
                ),
            )
        except (ValueError, discord.HTTPException) as error:
            await send_private(i, str(error))

    async def open_menu(self, i, *, command_center=None):
        command_center = menu_parent(command_center, i)
        if command_center is not None and not await command_center.interaction_check(i):
            return
        await self.authorize(i)
        await defer_menu(i, command_center)
        source = i.channel_id if isinstance(i.channel, discord.TextChannel) else None
        await self.show(i, source, command_center=command_center)

    async def show(self, i, source_id=None, notice=None, *, command_center=None):
        command_center = menu_parent(command_center, i)
        source = await self.channel(i.guild_id, source_id) if source_id else None
        view = Menu(self, i.user.id, i.guild_id, source, notice, command_center=command_center)
        attachments = uploads()
        try:
            await display_menu(i, view, attachments, command_center)
        finally:
            for item in attachments:
                item.close()


class Menu(discord.ui.LayoutView):
    def __init__(self, cog, owner, guild, source, notice=None, *, command_center=None):
        super().__init__(timeout=600)
        self.cog, self.owner, self.guild = cog, owner, guild
        self.command_center = command_center
        panel = discord.ui.Container(accent_colour=0xD6E2F3)
        panel.add_item(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem("attachment://ky_header_nsfw_flash.png")
            )
        )
        if notice:
            panel.add_item(discord.ui.TextDisplay(discord.utils.escape_mentions(str(notice))))
        panel.add_item(
            discord.ui.TextDisplay(
                "**SOURCE CHANNEL**\nChoose the channel to configure. "
                "Each channel has its own log destination and event choices."
            )
        )
        select = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder="Choose source channel",
            default_values=[discord.Object(id=source.id)] if source else [],
        )

        async def choose(i):
            await i.response.defer()
            await cog.show(i, select.values[0].id, command_center=self.command_center)

        select.callback = choose
        panel.add_item(discord.ui.ActionRow(select))
        if source:
            config = cog.logs.flash_config(guild, source.id)
            expected = json.dumps(config, sort_keys=True)

            async def change(i, **kwargs):
                await i.response.defer()
                try:
                    await cog.apply(i, source.id, expected=expected, **kwargs)
                    await cog.show(
                        i, source.id, "Settings saved.", command_center=self.command_center
                    )
                except (ValueError, discord.HTTPException) as error:
                    await cog.show(i, source.id, str(error), command_center=self.command_center)

            def row(title, description, label, callback):
                item = discord.ui.Button(label=label)
                item.callback = callback
                panel.add_item(discord.ui.Section(f"**{title}**\n{description}", accessory=item))

            row(
                "AGE-RESTRICTED CHANNEL",
                "Enabled" if source.is_nsfw() else "Disabled",
                "Disable" if source.is_nsfw() else "Enable",
                lambda i: change(i, adult=not source.is_nsfw()),
            )
            spoilers = cog.logs.spoiler_required(guild, source.id)
            row(
                "SPOILERED MEDIA",
                "Required" if spoilers else "Optional",
                "Make optional" if spoilers else "Require spoilers",
                lambda i: change(i, require_spoilers=not spoilers),
            )
            panel.add_item(
                discord.ui.TextDisplay(
                    "-# When required, image/video uploads and recognized media links "
                    "must be spoilered. "
                    "Unspoilered posts are removed as whole messages; "
                    "the author is asked to repost. "
                    "Media is never downloaded or reposted; these removals are not logged."
                )
            )
            panel.add_item(
                discord.ui.TextDisplay(
                    "**STAFF LOG CHANNEL**\nChoose a separate age-restricted staff channel. "
                    "No channel is created automatically."
                )
            )
            destination = discord.ui.ChannelSelect(
                channel_types=[discord.ChannelType.text],
                placeholder="Choose staff log channel",
                default_values=[discord.Object(id=config["destination"])]
                if config and config["destination"]
                else [],
            )
            destination.callback = lambda i: change(i, destination=destination.values[0].id)
            panel.add_item(discord.ui.ActionRow(destination))
            active = bool(config and config["enabled"])
            row(
                "FLASH LOGGING",
                "Enabled" if active else "Disabled",
                "Disable logs" if active else "Enable logs",
                lambda i: change(i, logging=not active),
            )
            event_types = discord.ui.Select(
                placeholder="Choose exactly what is logged",
                min_values=1,
                max_values=len(TYPES),
                options=[
                    discord.SelectOption(
                        label=label,
                        value=key,
                        default=key in (json.loads(config["types"]) if config else TYPES),
                    )
                    for key, label in TYPES.items()
                ],
            )
            event_types.callback = lambda i: change(i, types=list(event_types.values))
            panel.add_item(discord.ui.ActionRow(event_types))
        panel.add_item(discord.ui.Separator())
        panel.add_item(discord.ui.TextDisplay(PRIVACY))

        async def close(i):
            parent = menu_parent(self.command_center, i)
            if parent is not None:
                await defer_menu(i, parent)
                if getattr(parent, "back_menu", None) is not None:
                    await parent.back_menu.return_to(i)
                elif getattr(parent, "panel_manager", None):
                    await parent.panel_manager.reset(guild)
                else:
                    parent.section = "overview"
                    await parent.refresh(i)
                self.stop()
                return
            closed = discord.ui.LayoutView()
            closed.add_item(
                discord.ui.TextDisplay("NSFW Flash Logs menu closed. Settings are saved.")
            )
            await i.response.edit_message(content=None, view=closed, attachments=[])
            self.stop()

        nested = (
            command_center is not None and getattr(command_center, "back_menu", None) is not None
        )
        item = discord.ui.Button(
            label="Back" if nested else "Home" if command_center is not None else "Close"
        )
        item.callback = close
        panel.add_item(
            discord.ui.Section(
                "**BACK**\nReturn to Logs."
                if nested
                else "**COMMAND CENTER**\nReturn to the main menu."
                if command_center is not None
                else "**PRIVATE ADMIN CONTROLS**\nOnly you can use this menu.",
                accessory=item,
            )
        )
        group_sections(panel, "nsfw")
        section_dividers(panel)
        panel.add_item(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem("attachment://ky_footer_nsfw_flash.png")
            )
        )
        self.add_item(panel)

    async def interaction_check(self, i):
        self.command_center = menu_parent(self.command_center, i)
        if self.command_center is not None:
            if not await self.command_center.interaction_check(i):
                return False
        if i.user.id != self.owner or i.guild_id != self.guild:
            await send_private(i, "Open your own NSFW Flash Logs menu as a server administrator.")
            return False
        try:
            await self.cog.authorize(i)
            return True
        except ValueError as error:
            await send_private(i, str(error))
            return False

    async def on_error(self, i, error, item):
        await report_error(i, error)


async def setup(bot):
    await bot.add_cog(NSFWFlashLogs(bot))
