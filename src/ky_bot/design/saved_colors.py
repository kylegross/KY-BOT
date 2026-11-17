"""Reusable private named-color editor; applying copies a value into one target."""

import asyncio
import hashlib
import json

import discord

from ky_bot.errors import report_error, send_private

from .text_colour import text_colour


def validate_colors(value, *, limit=25):
    if not isinstance(value, dict) or len(value) > limit:
        raise ValueError(f"Save up to {limit} named custom colors.")
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
    def __init__(
        self, owner, guild, colors, targets, commit, *, allowed=None, draft=False, shared=False
    ):
        super().__init__(timeout=600)
        self.owner, self.guild = owner, guild
        self.shared, self.page = shared, 0
        self.limit = 100 if shared else 25
        self.colors, self.targets = validate_colors(colors, limit=self.limit), targets
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
            (notice + "\n\n" if notice else "")
            + (
                "**SERVER SAVED COLORS**\nShared across all menus in this server. "
                if self.shared
                else "**SAVED CUSTOM COLORS**\n"
            )
            + "Name colors, then choose where to apply them. "
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
        )

    async def refresh(self, i, notice=""):
        self.rebuild()
        await i.edit_original_response(content=self.content(notice), view=self)

    def rebuild(self):
        self.clear_items()
        if self.colors:
            names = list(self.colors)[self.page * 25 : (self.page + 1) * 25]
            picker = discord.ui.Select(
                placeholder="Choose a saved custom color",
                options=[
                    discord.SelectOption(
                        label=name, value=name, description=color, default=name == self.selected
                    )
                    for name in names
                    for color in [self.colors[name]]
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
            ("Add color", self.add, len(self.colors) >= self.limit),
            ("Edit saved color", self.edit, self.selected is None),
            ("Apply color", self.apply, self.selected is None),
        ):
            button = discord.ui.Button(label=label, disabled=disabled)
            button.callback = action
            self.add_item(button)
        if len(self.colors) > 25:
            for label, callback, disabled in (
                ("Previous colors", self.previous, self.page == 0),
                ("More colors", self.next_page, (self.page + 1) * 25 >= len(self.colors)),
            ):
                button = discord.ui.Button(label=label, disabled=disabled, row=3)
                button.callback = callback
                self.add_item(button)

    async def previous(self, i):
        await i.response.defer()
        self.page = max(0, self.page - 1)
        await self.refresh(i)

    async def next_page(self, i):
        await i.response.defer()
        self.page = min((len(self.colors) - 1) // 25, self.page + 1)
        await self.refresh(i)

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
            colors = validate_colors(colors, limit=self.limit)
            await self.commit(i, colors, None, None)
            self.colors, self.selected = colors, name
            self.page = list(colors).index(name) // 25
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


async def open_library(i, colors, targets, commit, *, allowed=None, draft=False, shared=False):
    repository = getattr(
        getattr(getattr(i, "client", None), "server_settings", None), "repository", None
    )
    if repository is not None:
        revision, saved = await repository.get_colors(i.guild_id)
        signature = hashlib.sha256(json.dumps(colors, sort_keys=True).encode()).hexdigest()
        if colors and not await repository.colors_imported(i.guild_id, signature):
            imported = dict(saved)
            for name, color in colors.items():
                if color in imported.values():
                    continue
                candidate, number = name, 2
                while candidate in imported and imported[candidate] != color:
                    suffix = f" ({number})"
                    candidate = name[: 40 - len(suffix)] + suffix
                    number += 1
                imported[candidate] = color
            if imported != saved:
                revision = await repository.save_colors(i.guild_id, imported, revision)
            await repository.mark_colors_imported(i.guild_id, signature)
            saved = imported
        original_commit = commit

        async def shared_commit(event, updated, target, color):
            nonlocal revision
            if target is None:
                revision = await repository.save_colors(event.guild_id, updated, revision)
            else:
                # Apply a copy to this setup; never copy the whole server library into it.
                await original_commit(event, {}, target, color)

        commit, colors, shared = shared_commit, saved, True
    view = ColorLibrary(
        i.user.id, i.guild_id, colors, targets, commit, allowed=allowed, draft=draft, shared=shared
    )
    if i.response.is_done():
        await i.followup.send(content=view.content(), view=view, ephemeral=True)
    else:
        await i.response.send_message(content=view.content(), view=view, ephemeral=True)
    return view
