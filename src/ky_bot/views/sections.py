"""Consistent dividers between inline menu sections, preserving existing grouping."""

import discord


def section_dividers(panel: discord.ui.Container) -> None:
    """Add one spaced rule between adjacent sections without duplicating separators."""
    children = list(panel.children)
    for child in children:
        panel.remove_item(child)
    previous = None
    for child in children:
        if isinstance(previous, discord.ui.Section) and isinstance(child, discord.ui.Section):
            panel.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        panel.add_item(child)
        previous = child
