"""Reusable private named-color editor; applying copies a value into one target."""

import asyncio

import discord

from ky_bot.errors import report_error, send_private

from .text_colour import text_colour


def validate_colors(value):
    if not isinstance(value, dict) or len(value) > 25:
        raise ValueError("Save up to 25 named custom colors in this setup.")
    result = {}
    for name, color in value.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 40:
            raise ValueError("Color names need 1–40 characters.")
        parsed = text_colour(color)
        if not parsed:
            raise ValueError("Enter a six-digit hex color, with or without #.")
        result[name] = parsed
    return result


class ColorModal(discord.ui.Modal):
    def __init__(self, library, existing=None):
        super().__init__(title="Edit saved color" if existing else "Add custom color")
        self.library, self.existing = library, existing
        self.name_field = discord.ui.TextInput(
            label="Color name", max_length=40, default=existing or ""
        )
        self.hex_field = discord.ui.TextInput(
            label="Hex color (with or without #)",
            max_length=7,
            default=library.colors.get(existing, ""),
        )
        self.add_item(self.name_field)
        self.add_item(self.hex_field)

    async def on_submit(self, i):
        if not await self.library.interaction_check(i):
            return
        await i.response.defer()
        try:
            await self.library.save_color(
                i, self.existing, str(self.name_field).strip(), str(self.hex_field)
            )
        except ValueError as error:
            await send_private(i, str(error))

    async def on_error(self, i, error):
        await report_error(i, error)


class ColorLibrary(discord.ui.View):
    def __init__(self, owner, guild, colors, targets, commit, *, allowed=None, draft=False):
        super().__init__(timeout=600)
        self.owner, self.guild = owner, guild
        self.colors, self.targets = validate_colors(colors), targets
        self.commit, self.allowed = commit, allowed
        self.selected = next(iter(self.colors), None)
        self.target = next(iter(targets))
        self.draft, self.lock = draft, asyncio.Lock()
        self.rebuild()

    async def interaction_check(self, i):
        if i.user.id != self.owner or i.guild_id != self.guild or not i.permissions.administrator:
            await send_private(i, "Only the administrator who opened this editor can use it.")
            return False
        return await self.allowed(i) if self.allowed else True

    def content(self, notice=""):
        name = discord.utils.escape_markdown(discord.utils.escape_mentions(self.selected or ""))
        return (
            "**SAVED CUSTOM COLORS**\nName colors, then choose where to apply them. "
            "Editing a saved color leaves previously applied colors unchanged.\n"
            + (
                "Changes stay in your design draft until you Apply design.\n"
                if self.draft
                else "Refresh the setup menu after closing this editor, then preview and publish.\n"
            )
            + (
                f"Selected: **{name}** · {self.colors[self.selected]}\n"
                if self.selected
                else "No saved colors yet. Use Add color to create one.\n"
            )
            + notice
        )

    async def refresh(self, i, notice=""):
        self.rebuild()
        await i.edit_original_response(content=self.content(notice), view=self)

    def rebuild(self):
        self.clear_items()
        if self.colors:
            picker = discord.ui.Select(
                placeholder="Choose a saved custom color",
                options=[
                    discord.SelectOption(
                        label=name, value=name, description=color, default=name == self.selected
                    )
                    for name, color in self.colors.items()
                ],
            )

            async def choose(i):
                await i.response.defer()
                self.selected = picker.values[0]
                await self.refresh(i)

            picker.callback = choose
            self.add_item(picker)
        target = discord.ui.Select(
            placeholder="Apply this color to…",
            options=[
                discord.SelectOption(label=label, value=key, default=key == self.target)
                for key, label in self.targets.items()
            ],
        )

        async def choose_target(i):
            await i.response.defer()
            self.target = target.values[0]
            await self.refresh(i)

        target.callback = choose_target
        self.add_item(target)
        for label, action, disabled in (
            ("Add color", self.add, len(self.colors) >= 25),
            ("Edit saved color", self.edit, self.selected is None),
            ("Apply color", self.apply, self.selected is None),
        ):
            button = discord.ui.Button(label=label, disabled=disabled)
            button.callback = action
            self.add_item(button)

    async def add(self, i):
        await i.response.send_modal(ColorModal(self))

    async def edit(self, i):
        await i.response.send_modal(ColorModal(self, self.selected))

    async def save_color(self, i, existing, name, color):
        async with self.lock:
            if existing is not None and existing not in self.colors:
                raise ValueError("This saved color changed. Reopen its editor.")
            if name in self.colors and name != existing:
                raise ValueError("That color name already exists. Choose another name.")
            colors = dict(self.colors)
            if existing is not None:
                colors.pop(existing)
            colors[name] = color
            colors = validate_colors(colors)
            await self.commit(i, colors, None, None)
            self.colors, self.selected = colors, name
            await self.refresh(i, "Color saved. Choose a target and Apply color to use it.")

    async def apply(self, i):
        await i.response.defer()
        async with self.lock:
            await self.commit(i, self.colors, self.target, self.colors[self.selected])
            await self.refresh(i, "Color applied to " + self.targets[self.target] + ".")

    async def on_error(self, i, error, item):
        if isinstance(error, ValueError):
            await send_private(i, str(error))
        else:
            await report_error(i, error)


async def open_library(i, colors, targets, commit, *, allowed=None, draft=False):
    view = ColorLibrary(
        i.user.id, i.guild_id, colors, targets, commit, allowed=allowed, draft=draft
    )
    if i.response.is_done():
        await i.followup.send(content=view.content(), view=view, ephemeral=True)
    else:
        await i.response.send_message(content=view.content(), view=view, ephemeral=True)
    return view
