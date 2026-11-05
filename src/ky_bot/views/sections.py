"""Consistent section rules and a separate bottom navigation area."""

import discord

LEFT_NAVIGATION = {"back", "home", "close", "close menu", "all guides", "back to editing", "cancel"}

NAVIGATION_LABELS = {"back", "home", "close", "close menu", "all guides", "all embeds"}


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
    """Space introductions, adjacent sections and navigation without doubling rules."""
    children = list(panel.children)
    for child in children:
        panel.remove_item(child)
    branded_groups = any(
        isinstance(child, discord.ui.MediaGallery)
        and "attachment://ky_strip_" in str(child.to_component_dict())
        for child in children
    )
    previous = None
    seen_control = False
    for child in children:
        is_control = isinstance(child, (discord.ui.Section, discord.ui.ActionRow))
        intro_boundary = (
            not seen_control
            and is_control
            and isinstance(previous, discord.ui.TextDisplay)
        )
        adjacent_sections = (
            not branded_groups
            and isinstance(previous, discord.ui.Section)
            and isinstance(child, discord.ui.Section)
        )
        footer_boundary = (
            previous is not None
            and not isinstance(previous, discord.ui.Separator)
            and navigation_item(child)
            and not (
                isinstance(previous, discord.ui.TextDisplay)
                and len(panel.children) >= 2
                and isinstance(panel.children[-2], discord.ui.Separator)
            )
            and not (
                isinstance(previous, discord.ui.MediaGallery)
                and "attachment://ky_strip_" in str(previous.to_component_dict())
            )
        )
        if intro_boundary:
            panel.add_item(
                discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.large)
            )
        elif adjacent_sections or footer_boundary:
            panel.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        seen_control = seen_control or is_control
        if (
            isinstance(child, discord.ui.Section)
            and isinstance(child.accessory, discord.ui.Button)
            and (child.accessory.label or "").casefold() in LEFT_NAVIGATION
        ):
            # Action rows start at the left; keep the original button and callback.
            for text in child.children:
                panel.add_item(text)
            navigation = discord.ui.ActionRow(child.accessory)
            panel.add_item(navigation)
            previous = navigation
        else:
            if isinstance(child, discord.ui.ActionRow) and navigation_item(child):
                items = list(child.children)
                for item in items:
                    child.remove_item(item)
                items.sort(
                    key=lambda item: (
                        not (
                            isinstance(item, discord.ui.Button)
                            and (item.label or "").casefold() in LEFT_NAVIGATION
                        )
                    )
                )
                for item in items:
                    child.add_item(item)
            panel.add_item(child)
            previous = child
