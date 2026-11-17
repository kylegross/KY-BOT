"""Own-image/video cleanup with private consent and durable page-by-page scanning."""

import asyncio
import logging
import uuid
from pathlib import PurePosixPath

import discord
from discord.ext import commands, tasks

from ky_bot.advice.command import administrator
from ky_bot.confessions.command import destination
from ky_bot.errors import report_error, send_private

from .store import Store

log = logging.getLogger(__name__)
SUFFIXES = {
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".avif",
    ".heic",
    ".heif",
    ".bmp",
    ".tif",
    ".tiff",
    ".svg",
    ".mp4",
    ".mov",
    ".webm",
    ".m4v",
    ".avi",
    ".mkv",
    ".mpeg",
    ".mpg",
    ".3gp",
}


def has_media(message):
    for attachment in getattr(message, "attachments", ()):
        mime = (getattr(attachment, "content_type", None) or "").split(";", 1)[0].strip().lower()
        suffix = PurePosixPath(attachment.filename.lower()).suffix
        if mime == "image/gif" or suffix in {".gif", ".gifv"}:
            continue
        if mime.startswith(("image/", "video/")) or (
            mime in {"", "application/octet-stream"} and suffix in SUFFIXES
        ):
            return True
    return False


class RemovalAction(
    discord.ui.DynamicItem[discord.ui.Button], template=r"ky:media-removal:(?P<guild>[0-9]{1,20})"
):
    def __init__(self, guild, label="Remove my media"):
        self.guild = int(guild)
        super().__init__(discord.ui.Button(label=label, custom_id=f"ky:media-removal:{guild}"))

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["guild"], item.label)

    async def callback(self, i):
        cog = i.client.get_cog("MediaRemoval")
        if not cog:
            return await send_private(i, "Media removal is temporarily unavailable.")
        try:
            if i.guild_id != self.guild:
                raise ValueError("Use the media removal panel in your server.")
            await cog.request(i)
        except ValueError as error:
            await send_private(i, str(error))
        except Exception as error:
            await report_error(i, error)


class Status(discord.ui.LayoutView):
    def __init__(self, cog, job):
        from ky_bot.views.sections import section_dividers

        super().__init__(timeout=600)
        self.cog, self.owner, self.guild, self.job_id = cog, job["owner"], job["guild"], job["id"]
        panel = discord.ui.Container(accent_colour=0xD6E2F3)
        active = job["state"] in ("discovering", "running")
        heading = (
            "Cleanup in progress"
            if active
            else "Cleanup paused"
            if job["state"] == "paused"
            else "Some media could not be removed"
            if job["state"] == "partial"
            else "Cleanup complete"
        )
        detail = (
            "I could not finish every channel or thread. Ask staff to check KY BOT’s permissions. "
            "Do not assume all media is gone."
            if job["state"] == "partial"
            else "Text-only messages, GIF-only posts, audio, stickers and links are kept."
        )
        panel.add_item(
            discord.ui.TextDisplay(
                f"**{heading}**\nPosts removed: **{job['deleted']:,}**\n{detail}"
            )
        )
        if active:
            panel.add_item(
                discord.ui.TextDisplay(
                    "Cleanup covers posts sent before you confirmed. It "
                    "continues if this private status expires or KY BOT "
                    "restarts. Use the panel again to check progress."
                )
            )
        refresh = discord.ui.Button(label="Refresh")

        async def reload(i):
            await i.response.defer()
            current = await cog.store.job(self.job_id, guild=i.guild_id, owner=i.user.id)
            if not current:
                return await send_private(i, "This cleanup status is no longer available.")
            await i.edit_original_response(view=Status(cog, current), content=None)

        refresh.callback = reload
        close = discord.ui.Button(label="Close")

        async def dismiss(i):
            await i.response.edit_message(
                content=None,
                view=discord.ui.LayoutView().add_item(
                    discord.ui.TextDisplay(
                        "Status closed. Use the media removal panel to check again."
                    )
                ),
            )

        close.callback = dismiss
        panel.add_item(discord.ui.ActionRow(refresh, close))
        section_dividers(panel, navigation_on_right=True)
        self.add_item(panel)

    async def interaction_check(self, i):
        if i.user.id == self.owner and i.guild_id == self.guild:
            return True
        await send_private(i, "Open your own cleanup status.")
        return False

    async def on_error(self, i, error, item):
        if isinstance(error, ValueError):
            await send_private(i, str(error))
        else:
            await report_error(i, error)


class Confirmation(discord.ui.LayoutView):
    def __init__(self, cog, i, revision, last=None):
        from ky_bot.views.sections import section_dividers

        super().__init__(timeout=300)
        self.owner, self.guild, self.revision = i.user.id, i.guild_id, revision
        self.used, self.lock = False, asyncio.Lock()
        panel = discord.ui.Container(accent_colour=0xD6E2F3)
        panel.add_item(
            discord.ui.TextDisplay(
                "**Remove your media across this server?**\nOnly your "
                "posts with uploaded images or videos will be deleted, "
                "including captions on those posts. Text-only messages, "
                "GIF-only posts, audio, stickers, links and other "
                "file-only posts are kept.\n**Deletion is permanent. "
                "Nothing is deleted until you confirm below.**"
            )
        )
        confirm = discord.ui.Button(label="Delete my media", style=discord.ButtonStyle.danger)

        async def start(event):
            if not await self.interaction_check(event):
                return
            await event.response.defer()
            async with self.lock:
                if self.used:
                    return await send_private(event, "This confirmation has already been used.")
                member = await event.guild.fetch_member(event.user.id)
                if member.bot:
                    raise ValueError("This cleanup is for members, not bot accounts.")
                job = await cog.store.request(
                    event.guild_id, event.user.id, event.id, self.revision
                )
                self.used = True
                await event.edit_original_response(view=Status(cog, job), content=None)
                self.stop()

        confirm.callback = start
        panel.add_item(
            discord.ui.Section(
                "**DELETE MY UPLOADED IMAGES & VIDEOS**\nThis cannot be undone.", accessory=confirm
            )
        )
        if last:
            result = discord.ui.Button(label="View last result")

            async def previous(event):
                await event.response.edit_message(view=Status(cog, last))

            result.callback = previous
            panel.add_item(discord.ui.ActionRow(result))
        cancel = discord.ui.Button(label="Cancel")

        async def cancel_action(event):
            if not await self.interaction_check(event):
                return
            async with self.lock:
                self.used = True
            await event.response.edit_message(
                view=discord.ui.LayoutView().add_item(
                    discord.ui.TextDisplay("Cancelled. No new deletion was started.")
                )
            )
            self.stop()

        cancel.callback = cancel_action
        panel.add_item(discord.ui.ActionRow(cancel))
        section_dividers(panel, navigation_on_right=True)
        self.add_item(panel)

    async def interaction_check(self, i):
        if i.user.id == self.owner and i.guild_id == self.guild:
            return True
        await send_private(i, "Open your own media removal confirmation.")
        return False

    async def on_error(self, i, error, item):
        if isinstance(error, ValueError):
            await send_private(i, str(error))
        else:
            await report_error(i, error)


class MediaRemoval(commands.Cog):
    def __init__(self, bot):
        self.bot, self.locks = bot, {}

    def lock(self, guild):
        return self.locks.setdefault(str(guild), asyncio.Lock())

    async def cog_load(self):
        self.store = await Store.open(
            self.bot.settings.database_path, self.bot.settings.database_url
        )
        self.bot.add_dynamic_items(RemovalAction)
        self.worker.start()

    async def cog_unload(self):
        self.worker.cancel()
        task = self.worker.get_task()
        if task:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.bot.remove_dynamic_items(RemovalAction)
        await self.store.close()

    async def request(self, i):
        if not i.guild or i.user.bot:
            raise ValueError("Use the media removal panel in your server.")
        await i.response.defer(ephemeral=True)
        revision, config = await self.store.config(i.guild_id)
        if (
            not config["enabled"]
            or i.channel_id != config["channel"]
            or config["published_channel"] != config["channel"]
            or not i.message
            or i.message.id != config["message"]
            or i.message.author.id != self.bot.user.id
        ):
            raise ValueError(
                "This media removal panel is disabled or has moved. Ask "
                "staff for the current panel."
            )
        member = await i.guild.fetch_member(i.user.id)
        target = destination(i.guild, config["channel"])
        if member.bot or not target.permissions_for(member).view_channel:
            raise ValueError("You cannot use this media removal panel.")
        last = await self.store.latest(i.guild_id, i.user.id)
        view = (
            Status(self, last)
            if last and last["state"] in ("discovering", "running")
            else Confirmation(self, i, revision, last)
        )
        await i.followup.send(view=view, ephemeral=True)

    async def post(self, target, data, *, private=False, message=None):
        from .views import render_panel

        guild_id = getattr(getattr(target, "guild", None), "id", None)
        if guild_id is None:
            guild_id = data["_guild"]
        _, config = await self.store.config(guild_id)
        view, attachments = render_panel(
            guild_id,
            config | {"panel": {k: v for k, v in data.items() if k != "_guild"}},
            preview=private,
        )
        try:
            if message:
                return await message.edit(
                    content=None,
                    embeds=[],
                    view=view,
                    attachments=attachments,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return await target.send(
                view=view,
                files=attachments,
                **({"ephemeral": True} if private else {}),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            for attachment in attachments:
                attachment.close()
            view.stop()

    async def publish(self, i, ident, expected):
        await administrator(i)
        async with self.lock(i.guild_id):
            revision, config = await self.store.config(i.guild_id)
            if expected != revision:
                raise ValueError("Settings changed. Refresh the editor before publishing.")
            if not config["channel"] or not config["enabled"]:
                raise ValueError("Choose the panel channel and enable media removal first.")
            channel = destination(i.guild, config["channel"])
            permissions = channel.permissions_for(i.guild.me)
            if not permissions.attach_files:
                raise ValueError("KY BOT needs Attach Files to publish this panel.")
            previous = None
            if config["message"] and config["published_channel"] == channel.id:
                try:
                    previous = await channel.fetch_message(config["message"])
                except discord.NotFound:
                    pass
                if previous and previous.author.id != self.bot.user.id:
                    raise ValueError("The saved panel is not owned by KY BOT.")
            result = await self.post(channel, config["panel"], message=previous)
            try:
                await self.store.configure(
                    i.guild_id, revision, message=result.id, published_channel=channel.id
                )
            except BaseException:
                if previous is None:
                    await result.delete()
                raise
            if config["message"] and config["published_channel"] != channel.id:
                try:
                    old = destination(i.guild, config["published_channel"])
                    old_message = await old.fetch_message(config["message"])
                    if old_message.author.id == self.bot.user.id:
                        await old_message.delete()
                except (ValueError, discord.HTTPException):
                    log.warning(
                        "Old media removal panel could not be removed in guild %s", i.guild_id
                    )
            return "Panel published. Members will confirm privately before cleanup starts."

    async def open_menu(self, i, *, command_center=None):
        from ky_bot.views.menu_context import defer_menu, menu_parent

        from .views import Admin

        await administrator(i)
        parent = menu_parent(command_center, i)
        await defer_menu(i, parent)
        await Admin(self, i.user.id, i.guild_id, parent=parent).show(i)

    async def discover(self, job, guild):
        for channel in await guild.fetch_channels():
            if not await self.store.touch(job["id"], job["lease"]):
                return
            if isinstance(channel, discord.CategoryChannel):
                continue
            if not hasattr(channel, "history") and not hasattr(channel, "archived_threads"):
                continue
            permissions = channel.permissions_for(guild.me)
            if not permissions.view_channel or not permissions.read_message_history:
                await self.store.issue(job["id"], f"access:{channel.id}")
                continue
            if hasattr(channel, "history"):
                await self.store.add_channel(job, channel.id)
            if hasattr(channel, "archived_threads"):
                try:
                    async for thread in channel.archived_threads(limit=None):
                        if not await self.store.touch(job["id"], job["lease"]):
                            return
                        await self.store.add_channel(job, thread.id)
                    if (
                        isinstance(channel, discord.TextChannel)
                        and channel.type == discord.ChannelType.text
                    ):
                        if not permissions.manage_threads:
                            await self.store.issue(job["id"], f"private-threads:{channel.id}")
                        async for thread in channel.archived_threads(
                            limit=None, private=True, joined=not permissions.manage_threads
                        ):
                            await self.store.add_channel(job, thread.id)
                except discord.Forbidden:
                    await self.store.issue(job["id"], f"archives:{channel.id}")
        try:
            for thread in await guild.active_threads():
                await self.store.add_channel(job, thread.id)
        except discord.Forbidden:
            await self.store.issue(job["id"], "active-threads")
        await self.store.state(job, "running")

    async def scan(self, job, guild, channel_id, cursor):
        try:
            channel = self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)
            if channel.guild.id != job["guild"]:
                raise ValueError("Wrong server.")
            permissions = channel.permissions_for(guild.me)
            if not all(
                getattr(permissions, p, False)
                for p in ("view_channel", "read_message_history", "manage_messages")
            ):
                raise ValueError("Missing channel access.")
            messages = [
                message
                async for message in channel.history(
                    limit=100, before=discord.Object(id=cursor), oldest_first=False
                )
            ]
            logs = getattr(getattr(self.bot.get_cog("AutoDelete"), "service", None), "logs", None)
            for message in messages:
                if (
                    message.author.id != job["owner"]
                    or message.id >= job["cutoff"]
                    or not has_media(message)
                ):
                    continue
                if not await self.store.touch(job["id"], job["lease"]):
                    return
                # Removing a forum/thread starter can remove other people's replies too.
                if isinstance(channel, discord.Thread) and message.id == channel.id:
                    await self.store.issue(job["id"], f"starter:{channel_id}:{message.id}")
                    continue
                if logs:
                    logs.begin_deletion(message)
                success = False
                try:
                    await message.delete()
                    success = True
                except discord.NotFound:
                    success = True
                    continue
                except discord.HTTPException as error:
                    if 400 <= error.status < 500 and error.status != 429:
                        await self.store.issue(job["id"], f"message:{channel_id}:{message.id}")
                        continue
                    raise
                finally:
                    if logs:
                        logs.finish_deletion(message, success)
                await self.store.deleted(job)
            await self.store.page_done(
                job,
                channel_id,
                min(m.id for m in messages) if messages else cursor,
                len(messages),
                state="done" if len(messages) < 100 else "pending",
            )
        except (ValueError, discord.Forbidden):
            await self.store.issue(job["id"], f"channel:{channel_id}")
            await self.store.page_done(job, channel_id, cursor, 0, state="blocked")
        except discord.NotFound:
            await self.store.page_done(job, channel_id, cursor, 0, state="done")

    async def step(self, job, guild):
        if job["state"] == "discovering":
            return await self.discover(job, guild)
        row = await self.store.next_channel(job["id"])
        if row:
            return await self.scan(job, guild, *row)
        await self.store.state(job, "partial" if await self.store.issues(job["id"]) else "complete")

    @tasks.loop(seconds=3)
    async def worker(self):
        for key, guild_id in await self.store.active():
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            _, config = await self.store.config(guild_id)
            if not config["enabled"] or not config["channel"]:
                continue
            token = uuid.uuid4().hex
            job = await self.store.claim(key, token)
            if not job:
                continue
            try:
                await self.step(job, guild)
            except Exception:
                log.exception("Media cleanup will retry from its saved page in guild %s", guild_id)
            finally:
                await self.store.release(key, token)

    @worker.before_loop
    async def before_worker(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(MediaRemoval(bot))
