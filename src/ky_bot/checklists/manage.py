"""Private editing, priority labels and manual ordering for checklist admins."""

import discord

from ky_bot.checklists.categories import allowed, validate
from ky_bot.checklists.inline import InlineView
from ky_bot.checklists.notices import notice

PRIORITIES = {"high": "🔴 High", "medium": "🟡 Medium", "low": "🟢 Low"}


class EditText(discord.ui.Modal):
    def __init__(self, service, item, kind):
        super().__init__(title="Edit Task" if kind == "task" else "Rename Category")
        self.service, self.item, self.kind = service, item, kind
        self.text = discord.ui.TextInput(
            label="Task text" if kind == "task" else "Category name",
            default=item["title"] if kind == "task" else item["name"],
            style=discord.TextStyle.paragraph if kind == "task" else discord.TextStyle.short,
            min_length=1,
            max_length=4000 if kind == "task" else 50,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction):
        if not await allowed(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.service.lock:
                item = self.item
                validate(self.service, interaction, item["channel"])
                if self.kind == "task":
                    self.service.store.edit_task(item["id"], item["revision"], title=str(self.text))
                    fresh = self.service.store.task(item["id"])
                else:
                    self.service.store.rename_category(
                        item["id"], item["channel"], item["revision"], str(self.text)
                    )
                    fresh = self.service.store.category(item["id"], item["channel"])
                self.service.wakeup.set()
            await interaction.followup.send(
                Noneview=ItemControls(self.service, fresh, self.kind), ephemeral=True
            )
        except ValueError as exc:
            await notice(interaction, str(exc), ephemeral=True)


class ItemControls(InlineView):
    def __init__(self, service, item, kind="task"):
        super().__init__(timeout=600)
        title = item["title"] if kind == "task" else item["name"]
        title = discord.utils.escape_markdown(discord.utils.escape_mentions(title[:500]))
        self.add_item(discord.ui.TextDisplay(f"**{title}**\n-# Edit or move this {kind}."))
        channel, ident, revision = item["channel"], item["id"], item["revision"]

        async def change(interaction, action=None, priority=None):
            await interaction.response.defer(ephemeral=True)
            try:
                async with service.lock:
                    validate(service, interaction, channel)
                    if priority is not None:
                        validate(service, interaction, channel, ident, revision)
                        service.store.edit_task(ident, revision, priority=priority)
                    else:
                        service.store.reorder(channel, kind, ident, action, revision)
                    fresh = (
                        service.store.task(ident)
                        if kind == "task"
                        else service.store.category(ident, channel)
                    )
                    service.wakeup.set()
                label = fresh["title"] if kind == "task" else fresh["name"]
                summary = discord.utils.escape_markdown(discord.utils.escape_mentions(label[:500]))
                if kind == "task":
                    summary += "\nPriority: " + PRIORITIES[fresh["priority"]]
                await interaction.edit_original_response(
                    content=None, view=ItemControls(service, fresh, kind)
                )
            except ValueError as exc:
                await notice(interaction, str(exc), ephemeral=True)

        if kind == "task":
            priority = discord.ui.Select(
                placeholder="Set priority",
                row=0,
                options=[
                    discord.SelectOption(
                        label=label, value=value, default=item["priority"] == value
                    )
                    for value, label in PRIORITIES.items()
                ],
            )

            async def set_priority(interaction):
                await change(interaction, priority=priority.values[0])

            priority.callback = set_priority
            self.add_item(priority)
        for action, label in [
            ("up", "Move Up"),
            ("down", "Move Down"),
            ("top", "Move to Top"),
            ("bottom", "Move to Bottom"),
        ]:
            button = discord.ui.Button(label=label, row=1)

            async def move(interaction, action=action):
                await change(interaction, action=action)

            button.callback = move
            self.add_item(button)
        edit = discord.ui.Button(label="Edit Task" if kind == "task" else "Rename Category", row=2)

        async def edit_text(interaction):
            try:
                validate(service, interaction, channel)
                fresh = (
                    service.store.task(ident)
                    if kind == "task"
                    else service.store.category(ident, channel)
                )
                await interaction.response.send_modal(EditText(service, fresh, kind))
            except ValueError as exc:
                await notice(interaction, str(exc), ephemeral=True)

        edit.callback = edit_text
        self.add_item(edit)
        if kind == "task":
            sort = discord.ui.Button(label="Sort Category by Priority", row=2)

            async def sort_priority(interaction):
                await change(interaction, action="priority")

            sort.callback = sort_priority
            self.add_item(sort)

    async def interaction_check(self, interaction):
        return await allowed(interaction)


class ManageCategories(InlineView):
    def __init__(self, service, channel, page=0):
        super().__init__(timeout=600)
        self.add_item(
            discord.ui.TextDisplay("**CATEGORIES**\nChoose a category to rename or reorder.")
        )
        categories = service.store.categories(channel)
        pages = max(1, (len(categories) + 24) // 25)
        page = max(0, min(page, pages - 1))
        select = discord.ui.Select(
            placeholder="Choose a category to rename or move",
            disabled=not categories,
            options=[
                discord.SelectOption(label=c["name"], value=str(c["id"]))
                for c in categories[page * 25 : (page + 1) * 25]
            ]
            or [discord.SelectOption(label="Add a category first", value="empty")],
        )

        async def choose(interaction):
            try:
                validate(service, interaction, channel)
                item = service.store.category(int(select.values[0]), channel)
                await interaction.response.edit_message(
                    content=None,
                    view=ItemControls(service, item, "category"),
                )
            except ValueError as exc:
                await notice(interaction, str(exc), ephemeral=True)

        select.callback = choose
        self.add_item(select)
        for delta, label in [(-1, "Previous"), (1, "Next")]:
            button = discord.ui.Button(
                label=label, disabled=page == 0 if delta < 0 else page == pages - 1
            )

            async def turn(interaction, delta=delta):
                try:
                    validate(service, interaction, channel)
                    await interaction.response.edit_message(
                        view=ManageCategories(service, channel, page + delta)
                    )
                except ValueError as exc:
                    await notice(interaction, str(exc), ephemeral=True)

            button.callback = turn
            self.add_item(button)

    async def interaction_check(self, interaction):
        return await allowed(interaction)
