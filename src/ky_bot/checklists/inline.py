"""Shared inline action sections for private checklist forms."""

import discord

from ky_bot.errors import report_error
from ky_bot.views.private_style import style_private_heading


class InlineView(discord.ui.LayoutView):
    def __init__(self, *, accent_colour=None, **kwargs):
        super().__init__(**kwargs)
        self.private_menu = kwargs.get("timeout", 180) is not None
        self.panel = None
        if accent_colour is None and self.private_menu:
            accent_colour = 0xBDC3CA
        if accent_colour is not None:
            self.panel = discord.ui.Container(accent_colour=accent_colour)
            super().add_item(self.panel)

    def add_item(self, item):
        if isinstance(item, discord.ui.TextDisplay) and self.private_menu:
            item.content = style_private_heading(item.content)
        if isinstance(item, discord.ui.Button):
            item.row = None
            label = item.label or "Action"
            item = discord.ui.Section(f"**{label.upper()}**", accessory=item)
        elif isinstance(
            item,
            (
                discord.ui.Select,
                discord.ui.ChannelSelect,
                discord.ui.RoleSelect,
                discord.ui.UserSelect,
                discord.ui.MentionableSelect,
            ),
        ):
            item.row = None
            item = discord.ui.ActionRow(item)
        if self.panel is not None:
            self.panel.add_item(item)
            return self
        return super().add_item(item)

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)
