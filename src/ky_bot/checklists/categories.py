"""Private category selection/forms opened from persistent checklist controls."""

import logging

import discord

from ky_bot.checklists.inline import InlineView
from ky_bot.checklists.notices import dismiss_later, notice


async def allowed(interaction):
    from ky_bot.checklists.permissions import access

    return await access(interaction)


def validate(service, interaction, channel, tid=None, revision=None):
    board = service.store.board(channel)
    if (
        not board
        or not interaction.guild
        or interaction.guild.id != board["guild"]
        or interaction.channel_id != channel
    ):
        raise ValueError("Use these controls in their checklist channel.")
    if tid is not None:
        task = service.store.task(tid)
        if task["channel"] != channel or (revision is not None and task["revision"] != revision):
            raise ValueError("This task changed. Reopen its category controls.")


class NewCategory(discord.ui.Modal, title="Create a checklist category"):
    name = discord.ui.TextInput(label="Category name", min_length=1, max_length=50)

    def __init__(self, service, channel, tid=None, revision=None, priority=None):
        super().__init__()
        self.service, self.channel, self.tid, self.revision = service, channel, tid, revision
        self.priority = priority

    async def on_submit(self, interaction):
        if not await allowed(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            async with self.service.lock:
                validate(self.service, interaction, self.channel, self.tid, self.revision)
                category = self.service.store.add_category(self.channel, str(self.name))
                if self.tid is not None:
                    self.service.store.move_task(
                        self.tid, category, self.revision, priority=self.priority
                    )
                self.service.wakeup.set()
            await notice(
                interaction,
                "Category saved and task filed! ♡" if self.tid is not None else "Category added! ♡",
                ephemeral=True,
            )
        except ValueError as exc:
            await notice(interaction, str(exc), ephemeral=True)


class CategoryPicker(InlineView):
    def __init__(self, service, task, page=0, selection=None):
        super().__init__(timeout=600)
        self.add_item(
            discord.ui.TextDisplay("**CATEGORY & PRIORITY**\nChoose both, then Save Task.")
        )
        channel, tid, revision = task["channel"], task["id"], task["revision"]
        self.selection = (
            dict(selection)
            if selection is not None
            else {"category": task["category_id"], "priority": task["priority"]}
        )
        categories = service.store.categories(channel)
        pages = max(1, (len(categories) + 23) // 24)
        page = min(max(0, page), pages - 1)
        select = discord.ui.Select(
            placeholder="What category does this belong in?",
            options=[
                discord.SelectOption(label=c["name"], value=str(c["id"]))
                for c in categories[page * 24 : (page + 1) * 24]
            ]
            + [discord.SelectOption(label="Uncategorized", value="none")],
        )
        for option in select.options:
            option.default = option.value == str(
                self.selection["category"] if self.selection["category"] is not None else "none"
            )

        async def choose(interaction):
            value = select.values[0]
            self.selection["category"] = None if value == "none" else int(value)
            await interaction.response.edit_message(
                view=CategoryPicker(service, task, page, self.selection)
            )

        select.callback = choose
        self.add_item(select)
        priority = discord.ui.Select(
            placeholder="Choose priority",
            options=[
                discord.SelectOption(
                    label=key.title(), value=key, default=key == self.selection["priority"]
                )
                for key in ("high", "medium", "low")
            ],
        )

        async def choose_priority(interaction):
            self.selection["priority"] = priority.values[0]
            await interaction.response.edit_message(
                view=CategoryPicker(service, task, page, self.selection)
            )

        priority.callback = choose_priority
        self.add_item(priority)
        save = discord.ui.Button(label="Save Task", style=discord.ButtonStyle.success)

        async def save_task(interaction):
            await interaction.response.defer(ephemeral=True)
            try:
                async with service.lock:
                    validate(service, interaction, channel, tid, revision)
                    service.store.move_task(
                        tid,
                        self.selection["category"],
                        revision,
                        priority=self.selection["priority"],
                    )
                    service.wakeup.set()
                await interaction.edit_original_response(
                    content=None,
                    view=InlineView().add_item(discord.ui.TextDisplay("Task filed! ♡")),
                )
                dismiss_later(interaction.delete_original_response)
            except ValueError as exc:
                await notice(interaction, str(exc), ephemeral=True)

        save.callback = save_task
        self.add_item(save)
        add = discord.ui.Button(label="Create new category", style=discord.ButtonStyle.primary)

        async def new(interaction):
            await interaction.response.send_modal(
                NewCategory(service, channel, tid, revision, priority=self.selection["priority"])
            )

        add.callback = new
        self.add_item(add)
        for delta, label in ((-1, "Previous categories"), (1, "More categories")):
            button = discord.ui.Button(
                label=label, disabled=(page == 0 if delta < 0 else page == pages - 1)
            )

            async def turn(interaction, delta=delta):
                try:
                    validate(service, interaction, channel, tid, revision)
                    await interaction.response.edit_message(
                        view=CategoryPicker(service, task, page + delta, self.selection)
                    )
                except ValueError as exc:
                    await notice(interaction, str(exc), ephemeral=True)

            button.callback = turn
            self.add_item(button)

    async def interaction_check(self, interaction):
        return await allowed(interaction)


class CategoryPrompt(InlineView):
    def __init__(self, service, task):
        super().__init__(timeout=None)
        title = discord.utils.escape_markdown(discord.utils.escape_mentions(task["title"][:400]))
        self.add_item(
            discord.ui.TextDisplay(f"**NEW TASK**\n{title}\n-# Choose its category and priority.")
        )
        button = discord.ui.Button(
            label="Category & Priority",
            style=discord.ButtonStyle.primary,
            custom_id=f"checklist:category:{task['id']}",
        )

        async def choose(interaction):
            try:
                async with service.lock:
                    validate(service, interaction, task["channel"], task["id"])
                    current = service.store.task(task["id"])
                    if not current["awaiting_category"]:
                        raise ValueError(
                            "This task is already filed. Select it from the checklist to move it."
                        )
                    # Keep the task reachable if the private picker is dismissed or expires.
                    service.store.move_task(current["id"], None, current["revision"])
                    current = service.store.task(current["id"])
                    service.wakeup.set()
                    await interaction.response.send_message(
                        None,
                        view=CategoryPicker(service, current),
                        ephemeral=True,
                    )
                    try:
                        await interaction.message.delete()
                    except discord.NotFound:
                        service.store.prompt(current["id"], None)
                    except discord.HTTPException:
                        logging.warning(
                            "Checklist category prompt deletion pending; will retry", exc_info=True
                        )
                    else:
                        service.store.prompt(current["id"], None)
            except ValueError as exc:
                await notice(interaction, str(exc), ephemeral=True)

        button.callback = choose
        self.add_item(button)

    async def interaction_check(self, interaction):
        return await allowed(interaction)


def parse_categories(value):
    if value is None:
        return []
    names = [" ".join(part.split()) for part in value.split(",")]
    if any(not 1 <= len(name) <= 50 or name.casefold() == "uncategorized" for name in names):
        raise ValueError(
            "Use comma-separated category names, 1–50 characters each. Uncategorized is built in."
        )
    return list({name.casefold(): name for name in names}.values())


def rows(items, categories):
    """Visible order and per-category numbers; database IDs never change."""
    result = []
    groups = [(c["id"], c["name"]) for c in categories]
    if not categories or any(t["category_id"] is None for t in items):
        groups.insert(0, (None, "Uncategorized" if categories else None))
    for cid, name in groups:
        tasks = [t for t in items if t["category_id"] == cid]
        result.extend((name, i, task) for i, task in enumerate(tasks, 1))
        if not tasks and name is not None:
            result.append((name, 0, None))
    return result
