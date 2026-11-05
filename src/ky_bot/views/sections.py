"""Consistent section rules and a separate bottom navigation area."""

import discord

NAVIGATION_LABELS = {"back", "home", "close", "close menu", "all guides"}


def navigation_item(item):
    if isinstance(item, discord.ui.Section):
        buttons = [item.accessory]
    elif isinstance(item, discord.ui.ActionRow):
        buttons = item.children
    else:
        return False
    return any(
        isinstance(button, discord.ui.Button)
        and (button.label or "").casefold() in NAVIGATION_LABELS
        for button in buttons
    )


def section_dividers(panel: discord.ui.Container) -> None:
    """Separate adjacent sections and footer navigation without doubling rules."""
    children = list(panel.children)
    for child in children:
        panel.remove_item(child)
    previous = None
    for child in children:
        adjacent_sections = isinstance(previous, discord.ui.Section) and isinstance(
            child, discord.ui.Section
        )
        footer_boundary = (
            previous is not None
            and not isinstance(previous, discord.ui.Separator)
            and navigation_item(child)
        )
        if adjacent_sections or footer_boundary:
            panel.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        panel.add_item(child)
        previous = child
