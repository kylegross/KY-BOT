"""Ticket section formatting follows the custom embed menu's text options."""

import discord
from ky_bot.custom_embeds.model import FIELD_STYLES, styled

PER_PAGE = 5
HEADING_STYLES = dict(FIELD_STYLES) | {"custom": "Custom Discord formatting"}
HEADING_STYLES["decorative"] = "KY BOT special heading"
HEADING_STYLES["special"] = "Special Unicode lettering"
BODY_STYLES = {
    "subtext": "Small muted text",
    **{key: value for key, value in HEADING_STYLES.items() if key != "decorative"},
}
PANEL_TEXT_STYLES = {"subtext": "Small muted text", **HEADING_STYLES}


def panel_text(panel, position):
    value = panel.get(position + "_text")
    return (panel["description"] if position == "before" else "") if value is None else value


def text_file(position):
    return "ticket_panel_" + position + ".png"


def heading(kind):
    value = kind.get("section_title")
    return kind["name"] if value is None else value


def button_label(kind):
    value = kind.get("button_label")
    return kind["name"] if value is None else value


def format_text(text, style):
    if not text.strip():
        return ""
    text = discord.utils.escape_mentions(text)
    if style in {"custom", "plain"}:
        return text
    if style == "subtext":
        return "\n".join("-# " + line for line in text.splitlines() if line.strip())
    return styled(text, style)


def visible_types(config, token, page=0):
    panel = config["panels"][token]
    entries = [
        (key, config["types"][key])
        for key in config["panels"][token]["types"]
        if key in config["types"] and config["types"][key]["enabled"]
    ]
    from .sections import content_budget

    content, images, characters = content_budget(panel)
    header = panel.get("header_mode", "ky") != "none"
    footer = panel.get("footer_mode", "ky") != "none"
    native_title = not (
        panel.get("header_mode", "ky") == "ky"
        or (panel.get("header_mode") == "custom" and panel.get("header_title"))
    )
    fixed = (
        1
        + 2 * header
        + 2 * footer
        + 2 * bool(panel["image"])
        + bool(panel["footer"].strip())
        + native_title
        + 4
        + 2
        + content
    )
    fixed_images = int(header) + int(footer) + images
    decorative = any(kind.get("heading_style", "bold") == "decorative" for _, kind in entries)
    size = min(PER_PAGE, (40 - fixed) // (5 if decorative else 4))
    if decorative:
        size = min(size, 10 - fixed_images)
    base = characters + len(panel["title"]) + 2 * len(panel["footer"]) + 200
    if size < 1 or fixed_images > 10 or base > 3800:
        raise ValueError(
            "This panel has too much section content for one message. "
            "Shorten the text, use fewer image headings, or remove a section."
        )
    costs = [
        len(format_text(heading(kind), kind.get("heading_style", "bold")))
        if kind.get("heading_style", "bold") != "decorative"
        else 0
        for _, kind in entries
    ]
    costs = [
        cost + len(format_text(kind["description"], kind.get("description_style", "subtext"))) + 10
        for cost, (_, kind) in zip(costs, entries)
    ]
    if costs:
        size = max(1, min(size, max(1, 3900 - base) // max(1, max(costs))))
    pages = max(1, (len(entries) + size - 1) // size)
    page = max(0, min(page, pages - 1))
    return entries[page * size : (page + 1) * size], page, pages


def heading_file(key):
    return "ticket_heading_" + key + ".png"


def published_layout(config, token):
    import copy

    panel_keys = (
        "title",
        "description",
        "color",
        "image",
        "footer",
        "types",
        "header_mode",
        "header_finish",
        "header_custom",
        "header_title",
        "footer_mode",
        "footer_finish",
        "footer_custom",
        "footer_content",
        "footer_banner_text",
        "footer_icon",
        "before_text",
        "before_style",
        "before_finish",
        "before_color",
        "before_heading",
        "before_heading_style",
        "before_sections",
        "after_text",
        "after_style",
        "after_finish",
        "after_color",
        "after_heading",
        "after_heading_style",
        "after_sections",
    )
    type_keys = (
        "name",
        "description",
        "emoji",
        "enabled",
        "section_title",
        "button_label",
        "heading_style",
        "description_style",
        "heading_finish",
        "heading_color",
    )
    panel = config["panels"][token]
    return copy.deepcopy(
        dict(
            panel={key: panel[key] for key in panel_keys if key in panel},
            types={
                key: {field: kind[field] for field in type_keys if field in kind}
                for key, kind in config["types"].items()
                if key in panel["types"]
            },
        )
    )
