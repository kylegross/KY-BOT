"""Admin-only message-to-task checklists and private task discussion threads."""

import asyncio
import json
import logging

import discord

from ky_bot.checklists.board import BoardView, safe
from ky_bot.checklists.categories import CategoryPrompt
from ky_bot.checklists.dots import PriorityDots
from ky_bot.checklists.inline import InlineView
from ky_bot.checklists.notices import dismiss_later, notice
from ky_bot.checklists.permissions import access, admin
from ky_bot.checklists.store import ChecklistStore


class DeleteTaskConfirm(InlineView):
    def __init__(self, service, task):
        super().__init__(timeout=120)
        self.add_item(
            discord.ui.TextDisplay(
                "**DELETE TASK**\nIts discussion and completion history will be kept."
            )
        )
        confirm = discord.ui.Button(label="Yes, delete task", style=discord.ButtonStyle.danger)

        async def remove(interaction):
            await interaction.response.defer(ephemeral=True)
            try:
                async with service.lock:
                    board = service.store.board(task["channel"])
                    if (
                        not board
                        or interaction.channel_id != task["channel"]
                        or interaction.guild.id != board["guild"]
                    ):
                        raise ValueError("Use this task’s own checklist channel.")
                    service.store.delete_task(task["id"], interaction.user.id, task["revision"])
                    service.wakeup.set()
                await interaction.edit_original_response(
                    content=None,
                    view=InlineView().add_item(
                        discord.ui.TextDisplay("Task deleted from the checklist. ♡")
                    ),
                )
                dismiss_later(interaction.delete_original_response)
            except ValueError as exc:
                await notice(interaction, str(exc), ephemeral=True)

        confirm.callback = remove
        self.add_item(confirm)
        cancel = discord.ui.Button(label="Keep task", style=discord.ButtonStyle.secondary)

        async def keep(interaction):
            await interaction.response.edit_message(
                content=None,
                view=InlineView().add_item(
                    discord.ui.TextDisplay("Task kept. Use Manage to reopen its controls.")
                ),
            )
            dismiss_later(interaction.delete_original_response)

        cancel.callback = keep
        self.add_item(cancel)

    async def interaction_check(self, interaction):
        return await access(interaction)


class ChecklistService:
    def __init__(self, client, store=None):
        self.client, self.store = client, store
        self.lock = asyncio.Lock()
        self.wakeup = asyncio.Event()
        self.priority_dots = PriorityDots()

    async def setup(self):
        self.store = self.store or ChecklistStore(
            self.client.settings.database_path.with_name("ky-checklists.sqlite3")
        )
        for board in self.store.boards():
            if board["message"]:
                self.client.add_view(BoardView(self, board), message_id=board["message"])
            self.store.update_board(board["channel"], dirty=1)
        for task in self.store.category_tasks():
            if task["awaiting_category"] and not task["deleted"] and task["category_prompt"]:
                self.client.add_view(CategoryPrompt(self, task), message_id=task["category_prompt"])
        self.worker = asyncio.create_task(self.run(), name="ky-checklist-worker")

    async def channel(self, cid):
        return self.client.get_channel(cid) or await self.client.fetch_channel(cid)

    def check_permissions(self, channel, *, logs=False):
        if not isinstance(channel, discord.TextChannel):
            raise ValueError("Use a server text channel for the checklist and its logs.")
        needed = ["view_channel", "send_messages", "embed_links"]
        if not logs:
            needed += [
                "manage_messages",
                "read_message_history",
                "attach_files",
                "create_private_threads",
                "send_messages_in_threads",
                "manage_threads",
            ]
        perms = channel.permissions_for(channel.guild.me)
        missing = [name for name in needed if not getattr(perms, name)]
        if missing:
            raise ValueError(f"Bot permissions missing in #{channel.name}: " + ", ".join(missing))

    async def configure(self, channel, log_channel, name=None, categories=()):
        self.check_permissions(channel)
        self.check_permissions(log_channel, logs=True)
        if channel.guild.id != log_channel.guild.id or channel.id == log_channel.id:
            raise ValueError("Choose a separate log channel in the same server.")
        self.store.create(channel.id, channel.guild.id, log_channel.id, name=name)
        for category in categories:
            self.store.add_category(channel.id, category)
        self.wakeup.set()

    async def on_message(self, message):
        if (
            not message.guild
            or message.author.bot
            or message.webhook_id
            or isinstance(message.channel, discord.Thread)
        ):
            return
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return
        if not admin(message.author, self.store.board(message.channel.id)) or not self.store.board(
            message.channel.id
        ):
            return
        title = message.content.strip()
        attachments = [dict(name=a.filename, url=a.url) for a in message.attachments]
        if not title:
            title = (
                "Attachments: " + ", ".join(a["name"] for a in attachments)
                if attachments
                else "Untitled task"
            )
        async with self.lock:
            if self.store.add(
                message.channel.id, message.id, message.author.id, title, attachments
            ):
                self.wakeup.set()

    async def open_task(self, interaction, channel_id, tid):
        from ky_bot.checklists.task_actions import TaskActions

        # Explicit check also protects calls outside BoardView.
        if not await access(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.lock:
                task = self.store.task(tid)
                if task["channel"] != channel_id or interaction.channel_id != channel_id:
                    raise ValueError("Select this task from its own checklist channel.")
                await interaction.followup.send(
                    view=TaskActions(self, task),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        except (ValueError, discord.HTTPException) as exc:
            logging.exception("Checklist task interaction failed")
            await notice(interaction, str(exc)[:1500], ephemeral=True)

    async def open_discussion(self, interaction, channel_id, tid):
        if not await access(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.lock:
                task = self.store.task(tid)
                board = self.store.board(channel_id)
                if (
                    not board
                    or board["guild"] != interaction.guild.id
                    or task["channel"] != channel_id
                    or interaction.channel_id != channel_id
                ):
                    raise ValueError("Open this task from its own checklist channel.")
                channel = await self.channel(channel_id)
                thread = await self.ensure_thread(task, channel, interaction.user)
            view = InlineView(timeout=600)
            view.add_item(discord.ui.Button(label="Go to Task Thread", url=thread.jump_url))
            await interaction.followup.send(view=view, ephemeral=True)
        except ValueError as exc:
            await notice(interaction, str(exc), ephemeral=True)
        except discord.HTTPException:
            logging.exception("Checklist discussion could not be opened for task %s", tid)
            await notice(
                interaction,
                "The discussion could not be opened. Check channel access, "
                "and the bot can create/manage private threads and send messages in threads. "
                "Your task management controls are still available.",
                ephemeral=True,
            )

    async def ensure_thread(self, task, channel, member=None):
        if task["done"]:
            raise ValueError(
                "This task is complete. Reopen the task before opening its discussion thread."
            )
        # Invite only the person using this task, or its creator during automatic
        # attachment preservation. Never expand the Checklist admin role into invites.
        if member is None:
            member = channel.guild.get_member(task["author"])
            if member is None:
                try:
                    member = await channel.guild.fetch_member(task["author"])
                except discord.NotFound:
                    member = None
        if member is not None and not channel.permissions_for(member).view_channel:
            raise ValueError("You need access to this checklist channel to open its discussion.")
        tid = task["id"]
        thread = None
        if task["thread"]:
            try:
                thread = await self.channel(task["thread"])
            except discord.NotFound:
                pass
        if thread is None:
            thread = await channel.create_thread(
                name=(f"Task {tid} • " + " ".join(task["title"].split()))[:100],
                type=discord.ChannelType.private_thread,
                invitable=False,
                auto_archive_duration=1440,
                reason="Checklist admin checklist task discussion",
            )
            self.store.thread(tid, thread.id)
            task["seeded"] = 0
        elif thread.archived:
            await thread.edit(
                archived=False,
                locked=False,
                reason="Checklist admin opened an unfinished task discussion",
            )
        if member is not None:
            await thread.add_user(member)
        if not task["seeded"]:
            embed = discord.Embed(
                title=f"✦ TASK #{tid} ✦", description=safe(task["title"])[:4000], color=0xE8C47C
            )
            embed.add_field(name="Added by", value=f"<@{task['author']}>")
            await thread.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            # Re-upload original attachments into the thread while their signed URLs are current.
            if json.loads(task["attachments"]):
                try:
                    source = await channel.fetch_message(task["source"])
                    for attachment in source.attachments:
                        file = await attachment.to_file()
                        try:
                            await thread.send(
                                file=file, allowed_mentions=discord.AllowedMentions.none()
                            )
                        finally:
                            file.close()
                except discord.NotFound:
                    await thread.send(
                        (
                            "The original attachment message is no longer available. You can upload"
                            " images here."
                        ),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            self.store.thread(tid, thread.id, seeded=True)
        return thread

    async def render_board(self, board):
        from ky_bot.checklists.artwork import board_artwork

        channel = await self.channel(board["channel"])
        dots = await self.priority_dots.for_guild(channel.guild)
        artwork = await asyncio.to_thread(
            board_artwork,
            board,
            self.store.categories(board["channel"]),
            self.store.tasks(board["channel"]),
        )
        view = BoardView(self, board, dots=dots)
        attachments = [
            discord.File(__import__("io").BytesIO(data), filename=name)
            for name, data in artwork.items()
        ]
        try:
            if board["message"]:
                try:
                    await channel.get_partial_message(board["message"]).edit(
                        content=None,
                        embeds=[],
                        view=view,
                        attachments=attachments,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    self.store.update_board(board["channel"], dirty=0)
                    return
                except discord.NotFound:
                    pass
            message = await channel.send(
                view=view, files=attachments, allowed_mentions=discord.AllowedMentions.none()
            )
            self.store.update_board(board["channel"], message=message.id, dirty=0)
        finally:
            for file in attachments:
                file.close()

    async def cleanup_source(self, task):
        board = self.store.board(task["channel"])
        # Do not delete the source until its checklist update has succeeded.
        if task["awaiting_category"] or not board or board["dirty"] or not board["message"]:
            return
        channel = await self.channel(task["channel"])
        try:
            source = await channel.fetch_message(task["source"])
        except discord.NotFound:
            self.store.cleaned(task["id"])
            return
        if source.author.id != task["author"] or source.author.bot:
            raise ValueError("Checklist source author does not match the saved task.")
        current_title = source.content.strip() or (
            "Attachments: " + ", ".join(a.filename for a in source.attachments)
            if source.attachments
            else "Untitled task"
        )
        if current_title != (task["source_title"] or task["title"]):
            raise ValueError(
                "Original task text changed after capture; leaving the message intact."
            )
        if source.attachments or json.loads(task["attachments"]):
            # Preserve attachments in the task's private thread before deleting their source.
            saved_names = [a["name"] for a in json.loads(task["attachments"])]
            if saved_names != [a.filename for a in source.attachments]:
                raise ValueError("Attachments changed after capture; leaving the message intact.")
            await self.ensure_thread(task, channel)
        try:
            await source.delete()
        except discord.NotFound:
            pass
        self.store.cleaned(task["id"])

    async def category_prompts(self):
        for task in self.store.category_tasks():
            try:
                channel = await self.channel(task["channel"])
                mid = task["category_prompt"]
                if task["awaiting_category"] and not task["deleted"]:
                    if mid:
                        try:
                            await channel.fetch_message(mid)
                            continue
                        except discord.NotFound:
                            self.store.prompt(task["id"], None)
                    message = await channel.send(
                        content=(None),
                        view=CategoryPrompt(self, task),
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    self.store.prompt(task["id"], message.id)
                elif mid and (not task["awaiting_category"] or task["deleted"]):
                    try:
                        await channel.get_partial_message(mid).delete()
                    except discord.NotFound:
                        pass
                    self.store.prompt(task["id"], None)
            except (discord.HTTPException, ValueError):
                logging.exception("Checklist category prompt pending; will retry")

    async def sync_task_threads(self):
        for task in self.store.pending_thread_sync():
            try:
                if task["thread"]:
                    thread = await self.channel(task["thread"])
                    closed = bool(task["done"])
                    if thread.archived != closed or thread.locked != closed:
                        await thread.edit(
                            archived=closed,
                            locked=closed,
                            reason="Checklist task completed"
                            if closed
                            else "Checklist task reopened",
                        )
                self.store.thread_synced(task["id"])
            except discord.NotFound:
                self.store.thread_synced(task["id"])
            except discord.HTTPException:
                logging.exception("Checklist thread status pending; will retry")

    async def tick(self):
        await self.sync_task_threads()
        await self.category_prompts()
        for board in self.store.boards():
            if board["dirty"]:
                try:
                    await self.render_board(board)
                except (discord.HTTPException, ValueError):
                    logging.exception("Checklist display pending; will retry")
        for task in self.store.pending_cleanup():
            try:
                await self.cleanup_source(task)
            except (discord.HTTPException, ValueError, OSError):
                logging.exception(
                    "Checklist source cleanup pending; original message retained for retry"
                )
        for event in self.store.pending_logs():
            try:
                board = self.store.board(event["channel"])
                if not board or not board["log_channel"] or not event["log_channel"]:
                    self.store.sent(event["id"])
                    continue
                channel = await self.channel(event["log_channel"])
                if channel.guild.id != board["guild"]:
                    raise ValueError("Checklist log must belong to the same server.")
                embed = discord.Embed(
                    description=(
                        f"**{safe(event['title'])[:3500]}** — checked off by <@{event['actor']}>"
                    ),
                    color=0xE8C47C,
                )
                embed.set_footer(text=f"Task #{event['task']} • Completion #{event['id']}")
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                self.store.sent(event["id"])
            except (discord.HTTPException, ValueError):
                logging.exception("Checklist completion log pending; will retry")

    async def run(self):
        await self.client.wait_until_ready()
        while not self.client.is_closed():
            self.wakeup.clear()
            try:
                async with self.lock:
                    await self.tick()
            except Exception:
                logging.exception("Checklist update failed; saved work will be retried")
            try:
                await asyncio.wait_for(self.wakeup.wait(), timeout=15)
            except TimeoutError:
                pass
