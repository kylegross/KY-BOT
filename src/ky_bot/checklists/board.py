"""Persistent inline task buttons and category sections, using Components V2."""

import discord

from ky_bot.checklists.categories import NewCategory, rows
from ky_bot.checklists.manage import ManageCategories
from ky_bot.checklists.permissions import access
from ky_bot.design.collection import COLOURS
from ky_bot.errors import report_error

PAGE_SIZE = 4  # Bound Components V2's 40-item budget, even with four separate categories.


def safe(value):
    return discord.utils.escape_markdown(discord.utils.escape_mentions(value))


def page_entries(board, tasks, categories):
    completed = board.get("task_view") == "completed"
    ordered = rows([t for t in tasks if bool(t["done"]) == completed], categories)
    pages = max(1, (len(ordered) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(max(board["page"], 0), pages - 1)
    return ordered[page * PAGE_SIZE : (page + 1) * PAGE_SIZE], page, pages


class BoardView(discord.ui.LayoutView):
    def __init__(self, service, board, *, dots=None):
        super().__init__(timeout=None)
        channel = board["channel"]
        tasks = service.store.tasks(channel)
        categories = service.store.categories(channel)
        entries, page, pages = page_entries(board, tasks, categories)
        dots = dots or {"high": "🔴", "medium": "🟡", "low": "🟢"}
        panel = discord.ui.Container(accent_colour=int(COLOURS[board["style"]][1:], 16))

        def gallery(filename):
            panel.add_item(
                discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://" + filename))
            )

        def action(label, key, callback, *, disabled=False):
            button = discord.ui.Button(
                label=label, custom_id=f"kycheck:{channel}:{key}", disabled=disabled
            )
            button.callback = callback
            return button

        gallery("checklist_header.png")
        panel.add_item(
            discord.ui.TextDisplay(
                '-# *Type a message within this channel to add a task to the checklist. '
                'Select the "Manage" button to the right of each task to edit, reorder, '
                "open its discussion thread, or mark the task as complete.*"
            )
        )

        async def switch(interaction):
            await interaction.response.defer()
            async with service.lock:
                current = service.store.board(channel)
                service.store.update_board(
                    channel,
                    task_view="open" if current["task_view"] == "completed" else "completed",
                    page=0,
                    dirty=1,
                )
                service.wakeup.set()

        completed = board["task_view"] == "completed"
        panel.add_item(
            discord.ui.Section(
                "**TASK VIEW**\n-# " + ("Completed tasks" if completed else "Open tasks"),
                accessory=action("Open tasks" if completed else "Completed", "view", switch),
            )
        )
        previous = object()
        for category_name, number, task in entries:
            name = category_name or "Uncategorized"
            category_id = (
                task["category_id"]
                if task
                else next((c["id"] for c in categories if c["name"] == name), None)
            )
            if name != previous:
                if previous.__class__ is str:
                    panel.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
                gallery(f"category_{category_id if category_id is not None else 'none'}.png")
                count = sum(not t["done"] and t["category_id"] == category_id for t in tasks)
                panel.add_item(
                    discord.ui.TextDisplay(f"-# *{count} open task{'s' if count != 1 else ''}*")
                )
            previous = name
            if task:

                async def manage(interaction, tid=task["id"]):
                    await service.open_task(interaction, channel, tid)

                title = safe(" ".join(task["title"].split())[:150])
                title = f"~~{title}~~" if task["done"] else title
                text = (
                    f"**{title}**\n-# {dots[task['priority']]} "
                    f"{task['priority'].title()} priority · <@{task['author']}>"
                )
                panel.add_item(
                    discord.ui.Section(
                        text, accessory=action("Manage", f"task:{task['id']}", manage)
                    )
                )
            else:
                panel.add_item(discord.ui.TextDisplay("-# No tasks yet."))
        if not entries:
            panel.add_item(discord.ui.TextDisplay("No tasks in this view yet."))
        panel.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))

        async def categories_menu(interaction):
            menu = ManageCategories(service, channel)
            add = discord.ui.Button(label="Add category")

            async def add_category(interaction):
                await interaction.response.send_modal(NewCategory(service, channel))

            add.callback = add_category
            menu.add_item(add)
            await interaction.response.send_message(
                None,
                view=menu,
                ephemeral=True,
            )

        panel.add_item(
            discord.ui.Section(
                "**CATEGORIES**\n-# Add, rename, reorder or sort by priority.",
                accessory=action("Manage", "categories", categories_menu),
            )
        )
        done = sum(bool(t["done"]) for t in tasks)
        panel.add_item(
            discord.ui.TextDisplay(
                f"-# {done} of {len(tasks)} complete · Page {page + 1} of {pages}"
            )
        )
        gallery("checklist_footer.png")
        buttons = []
        for delta, label in ((-1, "Previous"), (1, "Next")):

            async def turn(interaction, delta=delta):
                await interaction.response.defer()
                async with service.lock:
                    current = service.store.board(channel)
                    _, current_page, current_pages = page_entries(
                        current, service.store.tasks(channel), service.store.categories(channel)
                    )
                    service.store.update_board(
                        channel, page=max(0, min(current_pages - 1, current_page + delta)), dirty=1
                    )
                    service.wakeup.set()

            buttons.append(
                action(
                    label,
                    f"page:{delta}",
                    turn,
                    disabled=(page == 0 if delta < 0 else page == pages - 1),
                )
            )
        panel.add_item(discord.ui.ActionRow(*buttons))
        self.add_item(panel)

    async def interaction_check(self, interaction):
        return await access(interaction)

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)
