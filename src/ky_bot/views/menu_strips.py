"""Branded category strips and ordered groups for the main administration menus."""

import io
import re
from importlib.resources import files

import discord

from ky_bot.views.sections import navigation_item

GROUPS = {
    "askdm:roles": (
        ("ROLE PERMISSIONS", ("WHO CAN SEND", "WHO CAN RECEIVE")),
        ("RESTRICTIONS", ("BLOCKED FROM SENDING", "BLOCKED FROM RECEIVING")),
    ),
    "moderation": (("MEMBER MODERATION", ("KICK A MEMBER", "BAN A MEMBER", "TIME OUT A MEMBER")),),
    "nsfw": (
        ("CHANNEL & DELIVERY", ("SOURCE CHANNEL", "AGE-RESTRICTED CHANNEL", "SPOILERED MEDIA")),
        ("LOGGING & PRIVACY", ("STAFF LOG CHANNEL", "FLASH LOGGING", "WHAT IS LOGGED")),
    ),
    "settings:overview": (
        ("SERVER SETUP", ("COMMAND CENTER CHANNEL", "WELCOME EXPERIENCE")),
        ("ADMINISTRATION", ("MODERATION", "LOGS")),
        ("TOOLS & DESIGN", ("GENERAL SETTINGS", "MANAGE EMBEDS")),
        ("COMMUNITY", ("LEVELING", "CONFESSIONS", "ASK TO DM/FR")),
    ),
    "settings:general": (
        ("AUTOMATION", ("AUTODELETE", "AUTOTHREAD", "AUTOREACT")),
        ("MENU APPEARANCE", ("MENU APPEARANCE",)),
    ),
    "settings:welcome": (
        ("CHANNEL & DELIVERY", ("DESTINATION", "DELIVERY")),
        ("MESSAGE", ("WELCOME MESSAGE",)),
        ("APPEARANCE", ("APPEARANCE",)),
    ),
    "settings:logging": (
        ("SERVER EVENTS", ("SERVER EVENTS", "SERVER LOG DELIVERY")),
        ("MEMBERS & INVITES", ("MEMBERS & INVITES", "MEMBER LOG DELIVERY")),
    ),
    "leveling": (
        ("RANKINGS & MEMBER MANAGEMENT", ("LEADERBOARD", "MEMBER XP")),
        ("REWARDS & ANNOUNCEMENTS", ("ROLE REWARDS", "LEVEL-UP MESSAGES")),
        ("XP & ELIGIBILITY", ("XP RULES", "EXCLUSIONS", "TURN ON / OFF")),
    ),
    "custom_embeds": (
        ("SETUP", ("DESTINATION CHANNEL", "DRAFT NAME")),
        (
            "DESIGN",
            ("IMAGES", "CONTENT & TEXT STYLES", "HIGHLIGHT COLOR", "FORMATTING", "SECTIONS"),
        ),
        ("DELIVERY", ("STICKY DELIVERY",)),
        ("REVIEW & PUBLISH", ("PREVIEW & PUBLISH",)),
    ),
    "custom_embeds:library": (
        ("CREATE EMBED", ("CREATE EMBED",)),
        ("SAVED EMBEDS", ("SAVED EMBEDS", "DELETE EMBED")),
    ),
    "custom_embeds:selected": (("SAVED EMBEDS", ("SELECTED EMBED", "EDIT EMBED", "DELETE EMBED")),),
    "custom_embeds:content": (
        ("ADD STYLED TEXT", ("ADD STYLED TEXT", "NEW PASSAGE")),
        ("TITLE", ("TITLE STYLE", "TITLE")),
        ("MESSAGE", ("MESSAGE STYLE", "MESSAGE")),
        ("FOOTER TEXT", ("FOOTER TEXT STYLE", "FOOTER TEXT")),
    ),
    "custom_embeds:formatting": (
        ("TITLE", ("TITLE STYLE",)),
        ("FOOTER TEXT", ("FOOTER TEXT STYLE",)),
        ("DESIGN", ("DECORATIVE TEXT FINISH", "CUSTOM TEXT COLOR", "HIGHLIGHT COLOR")),
        ("SECTIONS", ("SECTIONS",)),
    ),
    "custom_embeds:sections": (
        ("SECTIONS", ("SECTIONS", "SELECTED SECTION", "REMOVE SECTION")),
        ("DESIGN", ("FORMATTING",)),
    ),
    "custom_embeds:images": (
        ("HEADER IMAGE", ("HEADER IMAGE", "REMOVE HEADER IMAGE")),
        ("FOOTER IMAGE", ("FOOTER IMAGE", "REMOVE FOOTER IMAGE")),
    ),
    "custom_embeds:style": (("DESIGN", ("HIGHLIGHT COLOR",)),),
    "custom_embeds:delivery": (
        ("DESTINATION", ("DESTINATION CHANNEL",)),
        ("DELIVERY", ("STICKY MESSAGE", "MESSAGE COUNT")),
    ),
    "custom_embeds:channel": (
        ("DESTINATION", ("DESTINATION CHANNEL",)),
        ("DELIVERY", ("STICKY MESSAGE", "MESSAGE COUNT")),
    ),
    "custom_embeds:publish": (
        ("REVIEW & PUBLISH", ("PRIVATE PREVIEW", "PUBLISH / UPDATE")),
        ("DELETE EMBED", ("DELETE EMBED",)),
    ),
    "custom_embeds:delete": (("DELETE EMBED", ("CONFIRM DELETION", "KEEP EMBED")),),
    "askdm": (
        ("REQUEST SETTINGS", ("REQUESTS",)),
        ("CHANNELS & LOGGING", ("REQUEST CHANNEL", "STAFF LOG CHANNEL")),
        ("ACCESS & PRIVACY", ("CHANNEL REQUEST VISIBILITY", "ROLE PERMISSIONS & RESTRICTIONS")),
        ("MEMBER PANEL", ("MEMBER PANEL",)),
    ),
    "help": (
        (
            "MEMBER GUIDES",
            (
                "ASK TO DM/FR",
                "CHECKLISTS",
                "CONFESSIONS",
                "LEVELING & REWARDS",
                "EMOJIS & STICKERS",
                "QUICK REFERENCE",
            ),
        ),
        (
            "STAFF GUIDES",
            ("MEMBER MODERATION", "COMMAND CENTER", "NSFW FLASH LOGS", "AUTOMATION & SETUP"),
        ),
    ),
    "automation": (
        ("DESTINATION", ("DESTINATION",)),
        (
            "RULE CONFIGURATION",
            ("CONFIGURE RULES", "CONFIGURE REACTIONS", "TURN ON / OFF", "CLEAN UP NOW"),
        ),
        ("SAVED RULES", ("SAVED RULES", "SERVER RULES", "REMOVE RULE")),
        ("ACTIVITY LOGS", ("ACTIVITY LOGS",)),
    ),
    "steal": tuple(
        (label, ()) for label in ("DESTINATION", "SELECT ASSETS", "CUSTOM NAMES", "RESULTS")
    ),
}


def filename(title):
    return "ky_strip_" + re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_") + ".png"


def strip_files(menu, style="dark_silver", text_color=None):
    return category_files((title for title, _ in GROUPS.get(menu, ())), style, text_color)


def category_files(titles, style="dark_silver", text_color=None):
    if style not in {"dark_silver", "gold", "silver_neon", "red_iridescent"}:
        raise ValueError("Choose a supported menu finish.")
    result = []
    for title in titles:
        data = (
            files("ky_bot.design")
            .joinpath("assets", "strips", style + "_" + filename(title))
            .read_bytes()
        )
        if text_color:
            from PIL import Image

            from ky_bot.design.footers import text_ink

            # Reuse the packaged mask, including its inset and trailing rule.
            with Image.open(io.BytesIO(data)) as image:
                ink = text_ink(image.getchannel("A"), style, text_color)
            stream = io.BytesIO()
            ink.save(stream, format="PNG")
            data = stream.getvalue()
        result.append(discord.File(io.BytesIO(data), filename=filename(title)))
    return result


def add_strip(panel, title):
    panel.add_item(
        discord.ui.MediaGallery(
            discord.MediaGalleryItem("attachment://" + filename(title), description=title)
        )
    )


def heading(item):
    if isinstance(item, discord.ui.Section):
        item = item.children[0]
    if isinstance(item, discord.ui.TextDisplay):
        first = item.content.split("\n", 1)[0]
        if first.startswith("**") and first.endswith("**"):
            return first[2:-2]
    return None


def group_sections(panel, menu):
    """Move whole control blocks, keeping each picker and explanatory text together."""
    groups = GROUPS.get(menu, ())
    names = {label for _, labels in groups for label in labels}
    if not any(heading(child) in names for child in panel.children):
        return
    prefix, blocks, tail = [], {}, []
    current = prefix
    for child in list(panel.children):
        if isinstance(child, discord.ui.Separator):
            continue
        label = heading(child)
        if navigation_item(child):
            current = tail
        elif label in names:
            current = blocks.setdefault(label, [])
        elif isinstance(child, discord.ui.TextDisplay) and child.content.startswith(
            ("-# Private admin controls", "-# Admin controls")
        ):
            current = tail
        elif isinstance(child, discord.ui.Section):
            current = tail
        elif isinstance(child, discord.ui.ActionRow) and any(
            isinstance(x, discord.ui.Button)
            and (x.label or "").lower() in {"home", "back", "close", "refresh"}
            for x in child.children
        ):
            current = tail
        elif isinstance(child, discord.ui.MediaGallery) and blocks:
            # Existing footer artwork belongs below the grouped controls.
            current = tail
        current.append(child)
    for child in list(panel.children):
        panel.remove_item(child)
    for child in prefix:
        panel.add_item(child)
    for title, labels in groups:
        if not any(label in blocks for label in labels):
            continue
        add_strip(panel, title)
        for label in labels:
            for child in blocks.get(label, []):
                if heading(child) == title:
                    text = child.children[0] if isinstance(child, discord.ui.Section) else child
                    _, separator, body = text.content.partition("\n")
                    if separator and body.strip():
                        text.content = body
                panel.add_item(child)
    if tail:
        for child in tail:
            panel.add_item(child)
