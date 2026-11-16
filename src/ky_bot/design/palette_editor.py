"""Private, non-persisting color previews shared by standard artwork editors."""

import asyncio
import io

import discord

from ky_bot.errors import report_error, send_private
from ky_bot.views.media import NoAltMediaGalleryItem

from .metallic import parse_palette, resolved_palette


class PaletteModal(discord.ui.Modal):
    def __init__(self, current, render, commit, allowed):
        super().__init__(title="Custom KY BOT metallic colors")
        self.render, self.commit, self.allowed = render, commit, allowed
        self.fields = {}
        for key, label in (
            ("base", "Base hex color"),
            ("highlight", "Highlight hex (blank = automatic)"),
            ("shadow", "Shadow hex (blank = automatic)"),
            ("accent", "Iridescent hex (blank = automatic)"),
        ):
            field = discord.ui.TextInput(
                label=label,
                default=(current or {}).get(key) or "",
                required=key == "base",
                max_length=7,
            )
            self.fields[key] = field
            self.add_item(field)

    async def on_submit(self, i):
        if not await self.allowed(i):
            return
        palette = parse_palette({key: field.value for key, field in self.fields.items()})
        await i.response.defer(ephemeral=True, thinking=True)
        images = await asyncio.to_thread(self.render, palette)
        view = PaletteReview(palette, images, self.commit, self.allowed)
        files = [discord.File(io.BytesIO(data), filename=name) for name, data in images]
        try:
            await i.edit_original_response(
                view=view, attachments=files, allowed_mentions=discord.AllowedMentions.none()
            )
        finally:
            for file in files:
                file.close()

    async def on_error(self, i, error):
        if isinstance(error, ValueError):
            await send_private(i, str(error))
        else:
            await report_error(i, error)


class PaletteReview(discord.ui.LayoutView):
    def __init__(self, palette, images, commit, allowed):
        super().__init__(timeout=600)
        self.palette, self.commit, self.allowed = palette, commit, allowed
        resolved = resolved_palette(palette)
        panel = discord.ui.Container(accent_colour=int(palette["base"][1:], 16))
        panel.add_item(
            discord.ui.TextDisplay(
                "**CUSTOM METALLIC COLOR PREVIEW**\n"
                "Nothing has been saved. Use these colors or discard this preview.\n"
                + " · ".join(
                    key.title() + ": #" + "".join(f"{c:02X}" for c in rgb)
                    for key, rgb in resolved.items()
                )
            )
        )
        for name, _ in images:
            panel.add_item(discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://" + name)))
        use = discord.ui.Button(label="Use these colors", style=discord.ButtonStyle.success)
        cancel = discord.ui.Button(label="Discard", style=discord.ButtonStyle.secondary)
        use.callback, cancel.callback = self.use, self.discard
        panel.add_item(discord.ui.ActionRow(use, cancel))
        self.add_item(panel)

    async def interaction_check(self, i):
        return await self.allowed(i)

    async def use(self, i):
        await self.commit(i, self.palette)
        self.stop()

    async def discard(self, i):
        view = discord.ui.LayoutView(timeout=1)
        view.add_item(discord.ui.TextDisplay("Custom color preview discarded. Nothing was saved."))
        await i.response.edit_message(view=view, attachments=[])
        self.stop()

    async def on_error(self, i, error, item):
        if isinstance(error, ValueError):
            await send_private(i, str(error))
        else:
            await report_error(i, error)
