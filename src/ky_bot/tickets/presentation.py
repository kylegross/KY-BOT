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
    entries = [
        (key, config["types"][key])
        for key in config["panels"][token]["types"]
        if key in config["types"] and config["types"][key]["enabled"]
    ]
    pages = max(1, (len(entries) + PER_PAGE - 1) // PER_PAGE)
    page = max(0, min(page, pages - 1))
    return entries[page * PER_PAGE : (page + 1) * PER_PAGE], page, pages


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
