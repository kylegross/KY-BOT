"""Consistent section rules and a separate bottom navigation area."""

import discord

LEFT_NAVIGATION = {"back", "close", "close menu", "all guides", "back to editing", "cancel"}

NAVIGATION_LABELS = LEFT_NAVIGATION | {"home", "all embeds"}


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
    # Home always belongs beside its destination text, as a right-side accessory.
    home_text = "**COMMAND CENTER**\nReturn to the main menu."
    for child in list(panel.children):
        if (
            isinstance(child, discord.ui.Section)
            and isinstance(child.accessory, discord.ui.Button)
            and (child.accessory.label or "").casefold() == "home"
        ):
            child.children[0].content = home_text
    children = []
    for child in panel.children:
        if isinstance(child, discord.ui.ActionRow):
            homes = [
                button
                for button in child.children
                if isinstance(button, discord.ui.Button)
                and (button.label or "").casefold() == "home"
            ]
            for button in homes:
                child.remove_item(button)
            if child.children:
                children.append(child)
            children.extend(discord.ui.Section(home_text, accessory=button) for button in homes)
        else:
            children.append(child)
    # Keep picker descriptions adjacent to the picker, without spacer components.
    children = [
        child
        for index, child in enumerate(children)
        if not (
            isinstance(child, discord.ui.Separator)
            and index > 0
            and index + 1 < len(children)
            and isinstance(children[index - 1], (discord.ui.TextDisplay, discord.ui.Section))
            and isinstance(children[index + 1], discord.ui.ActionRow)
            and any(
                isinstance(
                    control,
                    (
                        discord.ui.Select,
                        discord.ui.ChannelSelect,
                        discord.ui.RoleSelect,
                        discord.ui.UserSelect,
                        discord.ui.MentionableSelect,
                    ),
                )
                for control in children[index + 1].children
            )
        )
    ]
    # Footer artwork closes the menu after all navigation controls.
    footers = [
        child
        for child in children
        if isinstance(child, discord.ui.MediaGallery)
        and any("footer" in item.media.url.rsplit("/", 1)[-1].lower() for item in child.items)
    ]
    children = [child for child in children if child not in footers] + footers
    for child in panel.children:
        panel.remove_item(child)
    branded_groups = any(
        isinstance(child, discord.ui.MediaGallery)
        and "attachment://ky_strip_" in str(child.to_component_dict())
        for child in children
    )
    previous = None
    for child in children:
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
            and not navigation_item(previous)
        )
        if adjacent_sections or footer_boundary:
            panel.add_item(
                discord.ui.Separator(
                    spacing=discord.SeparatorSpacing.small
                    if footer_boundary
                    else discord.SeparatorSpacing.large
                )
            )
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
