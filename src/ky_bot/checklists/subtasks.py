"""Persistent inline sub-task panel inside a private task discussion."""

import discord

from ky_bot.checklists.board import category_heading, safe
from ky_bot.checklists.inline import InlineView
from ky_bot.checklists.notices import notice
from ky_bot.checklists.permissions import admin
from ky_bot.design.collection import COLOURS
from ky_bot.errors import report_error


def validate(service, interaction, tid):
    task = service.store.task(tid)
    board = service.store.board(task["channel"])
    if not (
        interaction.guild
        and board
        and board["guild"] == interaction.guild.id
        and task["thread"] == interaction.channel_id
        and admin(interaction.user, board)
    ):
        raise ValueError(
            "Only this checklist’s administrators and authorized role can manage sub-tasks."
        )
    return task


class SubtaskModal(discord.ui.Modal):
    def __init__(self, service, tid, item=None):
        super().__init__(title="Edit sub-task" if item else "Add sub-task", timeout=300)
        self.service, self.tid, self.item = service, tid, item
        self.name = discord.ui.TextInput(
            label="Sub-task", max_length=400, default=item["title"] if item else None
        )
        self.add_item(self.name)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.service.lock:
                validate(self.service, interaction, self.tid)
                if self.item:
                    current = self.service.store.subtask(self.item["id"])
                    if current["task"] != self.tid:
                        raise ValueError("Use this sub-task’s own discussion.")
                    self.service.store.change_subtask(
                        self.item["id"], self.item["revision"], title=str(self.name)
                    )
                else:
                    self.service.store.add_subtask(self.tid, str(self.name), interaction.user.id)
                self.service.wakeup.set()
            await notice(interaction, "Sub-task saved.", ephemeral=True)
        except ValueError as error:
            await notice(interaction, str(error), ephemeral=True)

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class SubtaskActions(InlineView):
    def __init__(self, service, tid, item):
        super().__init__(timeout=600)
        self.service, self.tid = service, tid
        self.add_item(discord.ui.TextDisplay(f"## {safe(item['title'])}"))

        async def edit(interaction):
            await interaction.response.send_modal(SubtaskModal(service, tid, item))

        button = discord.ui.Button(label="Edit text")
        button.callback = edit
        self.add_item(button)
        for label, values in (
            ("Reopen" if item["done"] else "Complete", {"done": not bool(item["done"])}),
            ("Move up", {"move": "up"}),
            ("Move down", {"move": "down"}),
            ("Delete sub-task", {"delete": True}),
        ):
            button = discord.ui.Button(label=label)

            async def change(interaction, values=values):
                await interaction.response.defer()
                try:
                    async with service.lock:
                        validate(service, interaction, tid)
                        current = service.store.subtask(item["id"])
                        if current["task"] != tid:
                            raise ValueError("Use this sub-task’s own discussion.")
                        service.store.change_subtask(item["id"], item["revision"], **values)
                        service.wakeup.set()
                    await interaction.edit_original_response(
                        view=InlineView().add_item(discord.ui.TextDisplay("Sub-task updated."))
                    )
                except ValueError as error:
                    await notice(interaction, str(error), ephemeral=True)

            button.callback = change
            self.add_item(button)

    async def interaction_check(self, interaction):
        try:
            validate(self.service, interaction, self.tid)
            return True
        except ValueError as error:
            await notice(interaction, str(error), ephemeral=True)
            return False


class DiscussionPanel(discord.ui.LayoutView):
    def __init__(self, service, task):
        super().__init__(timeout=None)
        self.service, self.tid = service, task["id"]
        board = service.store.board(task["channel"])
        items = service.store.subtasks(task["id"])
        page = min(task["subtask_page"], max(0, (len(items) - 1) // 4))
        panel = discord.ui.Container(accent_colour=int(COLOURS[board["style"]][1:], 16))
        panel.add_item(
            discord.ui.TextDisplay(
                f"## {category_heading(service.store, task)}\n{safe(task['title'][:1000])}\n"
                f"-# Added by <@{task['author']}>"
            )
        )
        panel.add_item(
            discord.ui.TextDisplay(
                f"**SUB-TASKS**\n*{sum(bool(i['done']) for i in items)} of {len(items)} complete*"
            )
        )
        for item in items[page * 4 : page * 4 + 4]:
            button = discord.ui.Button(
                label="Manage",
                custom_id=f"kysub:{self.tid}:{item['id']}",
                disabled=bool(task["done"]),
            )

            async def manage(interaction, item=item):
                await interaction.response.send_message(
                    view=SubtaskActions(service, task["id"], item), ephemeral=True
                )

            button.callback = manage
            panel.add_item(
                discord.ui.Section(
                    f"{'✓' if item['done'] else '○'} {safe(item['title'])}", accessory=button
                )
            )
        if not items:
            panel.add_item(discord.ui.TextDisplay("-# No sub-tasks yet."))
        button = discord.ui.Button(
            label="✚ Add sub-task",
            style=discord.ButtonStyle.success,
            custom_id=f"kysub:{self.tid}:add",
            disabled=bool(task["done"]),
        )

        async def add(interaction):
            await interaction.response.send_modal(SubtaskModal(service, self.tid))

        button.callback = add
        panel.add_item(discord.ui.Section("Add a step to this task.", accessory=button))
        if len(items) > 4:
            buttons = []
            for label, delta, disabled in (
                ("Previous", -1, page == 0),
                ("Next", 1, (page + 1) * 4 >= len(items)),
            ):
                button = discord.ui.Button(
                    label=label, custom_id=f"kysub:{self.tid}:{label}", disabled=disabled
                )

                async def navigate(interaction, delta=delta):
                    await interaction.response.defer()
                    async with service.lock:
                        current = validate(service, interaction, self.tid)
                        pages = max(0, (len(service.store.subtasks(self.tid)) - 1) // 4)
                        service.store.subtask_page(
                            self.tid, min(current["subtask_page"], pages) + delta
                        )
                        service.wakeup.set()

                button.callback = navigate
                buttons.append(button)
            panel.add_item(discord.ui.ActionRow(*buttons))
        self.add_item(panel)

    async def interaction_check(self, interaction):
        try:
            validate(self.service, interaction, self.tid)
            self.service.store.touch_thread(self.tid)
            return True
        except ValueError as error:
            await notice(interaction, str(error), ephemeral=True)
            return False

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)
