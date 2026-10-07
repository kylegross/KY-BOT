"""Private inline task actions, with existing VICE editing workflows preserved."""

import discord

from ky_bot.checklists.board import safe
from ky_bot.checklists.categories import CategoryPicker, validate
from ky_bot.checklists.manage import ItemControls
from ky_bot.checklists.notices import notice
from ky_bot.checklists.permissions import access
from ky_bot.design.collection import COLOURS
from ky_bot.errors import report_error


class TaskActions(discord.ui.LayoutView):
    def __init__(self, service, task):
        super().__init__(timeout=600)
        board = service.store.board(task["channel"])
        panel = discord.ui.Container(accent_colour=int(COLOURS[board["style"]][1:], 16))
        panel.add_item(discord.ui.TextDisplay(f"**{safe(task['title'][:500])}**"))

        def row(title, description, label, callback):
            button = discord.ui.Button(label=label)
            button.callback = callback
            panel.add_item(discord.ui.Section(f"**{title}**\n-# {description}", accessory=button))

        async def toggle(interaction):
            await interaction.response.defer()
            try:
                async with service.lock:
                    validate(service, interaction, task["channel"], task["id"], task["revision"])
                    service.store.set_done(
                        task["id"], interaction.user.id, task["revision"], not bool(task["done"])
                    )
                    current = service.store.task(task["id"])
                    service.wakeup.set()
                await interaction.edit_original_response(view=TaskActions(service, current))
            except ValueError as exc:
                await notice(interaction, str(exc))

        async def discussion(interaction):
            await service.open_discussion(interaction, task["channel"], task["id"])

        async def category(interaction):
            try:
                validate(service, interaction, task["channel"], task["id"])
                current = service.store.task(task["id"])
                await interaction.response.send_message(
                    None,
                    view=CategoryPicker(service, current),
                    ephemeral=True,
                )
            except ValueError as exc:
                await notice(interaction, str(exc))

        async def edit(interaction):
            try:
                validate(service, interaction, task["channel"], task["id"])
                await interaction.response.send_message(
                    None,
                    view=ItemControls(service, service.store.task(task["id"])),
                    ephemeral=True,
                )
            except ValueError as exc:
                await notice(interaction, str(exc))

        async def delete(interaction):
            from ky_bot.checklists.service import DeleteTaskConfirm

            try:
                validate(service, interaction, task["channel"], task["id"])
                current = service.store.task(task["id"])
                await interaction.response.send_message(
                    view=DeleteTaskConfirm(service, current),
                    ephemeral=True,
                )
            except ValueError as exc:
                await notice(interaction, str(exc))

        async def close(interaction):
            await interaction.response.edit_message(
                view=discord.ui.LayoutView().add_item(
                    discord.ui.TextDisplay("Task controls closed.")
                )
            )

        row(
            "COMPLETION",
            "Mark complete or reopen this task.",
            "Reopen" if task["done"] else "Complete",
            toggle,
        )
        row(
            "DISCUSSION",
            "Private thread with the original text and attachments.",
            "Open thread",
            discussion,
        )
        row(
            "CATEGORY & PRIORITY",
            "Choose a category and high, medium or low priority.",
            "Edit",
            category,
        )
        row("TEXT & ORDER", "Edit the text or move the task within its category.", "Edit", edit)
        row("DELETE TASK", "Discussion and completion history are retained.", "Delete", delete)
        row("PRIVATE CONTROLS", "Visible only to you.", "Close", close)
        self.add_item(panel)

    async def interaction_check(self, interaction):
        return await access(interaction)

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)
