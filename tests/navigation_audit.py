"""Check final rendered trees independently of each menu's layout implementation."""

import discord

LABELS = {
    "refresh",
    "home",
    "back",
    "close",
    "close menu",
    "cancel",
    "all embeds",
    "all guides",
    "back to editing",
    "back to general",
    "back to logs",
    "back to welcome",
    "return home",
    "return to home",
    "return to main menu",
    "main menu",
    "return to editing",
    "cancel setup",
}


def nav(item):
    if isinstance(item, discord.ui.Section):
        buttons = [item.accessory]
        if getattr(item.accessory, "label", "").casefold() == "refresh" and item.children[
            0
        ].content.split("\n", 1)[0].strip("* ").casefold() not in {
            "refresh",
            "refresh menu",
            "reload settings",
        }:
            return False
    elif isinstance(item, discord.ui.ActionRow):
        buttons = item.children
    else:
        return False
    return any(
        isinstance(button, discord.ui.Button) and (button.label or "").casefold() in LABELS
        for button in buttons
    )


def assert_footer_dividers(test, view):
    for container in view.walk_children():
        if not isinstance(container, discord.ui.Container):
            continue
        children = list(container.children)
        for index, child in enumerate(children):
            if not nav(child):
                continue
            before = children[:index]
            if before and nav(before[-1]):
                continue
            if (
                len(before) >= 2
                and isinstance(before[-1], discord.ui.TextDisplay)
                and nav(before[-2])
            ):
                continue
            if before and isinstance(before[-1], discord.ui.TextDisplay):
                before = before[:-1]
            labels = (
                [
                    item.label
                    for item in child.walk_children()
                    if isinstance(item, discord.ui.Button)
                ]
                if isinstance(child, discord.ui.Section)
                else [getattr(item, "label", "") for item in child.children]
            )
            context = f"{type(view).__module__}.{type(view).__name__} {labels}"
            test.assertTrue(before, context + " has no divider")
            test.assertIsInstance(before[-1], discord.ui.Separator, context)
            test.assertTrue(before[-1].visible, context + " has an invisible divider")
