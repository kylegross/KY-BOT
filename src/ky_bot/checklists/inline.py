"""Shared inline action sections for private checklist forms."""

import discord

from ky_bot.errors import report_error


class InlineView(discord.ui.LayoutView):
    def add_item(self, item):
        if isinstance(item, discord.ui.Button):
            item.row = None
            label = item.label or "Action"
            item = discord.ui.Section(f"**{label.upper()}**", accessory=item)
        elif isinstance(item, discord.ui.Select):
            item.row = None
            item = discord.ui.ActionRow(item)
        return super().add_item(item)

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)
