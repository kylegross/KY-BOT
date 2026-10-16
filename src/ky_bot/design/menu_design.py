"""Shared server menu appearance; image rendering is reused by drafts and live menus."""

import base64
import io

import discord
from PIL import Image, ImageOps

from ky_bot.checklists.artwork import png, title_image
from ky_bot.design.collection import COLOURS, floral_background
from ky_bot.design.footers import centered_overlay, read_upload, rectangular_frame
from ky_bot.design.headers import render_header
from ky_bot.design.typography import text_mask

FINISHES = {"gold": "Gold", "dark_silver": "Dark silver", "silver_neon": "Multicolor silver"}
AUTOMATION_KEYS = ("autodelete", "autothread", "autoreact")


def default_profile(key):
    return dict(
        style="gold"
        if key.startswith("checklist")
        else "silver_neon"
        if key.startswith("settings") or key == "welcome_image" or key in AUTOMATION_KEYS
        else "dark_silver",
        height=280,
        title=None,
        framed=True,
        footer_text="",
        header_background=None,
        footer_background=None,
        footer_icon=None,
        dim=35,
    )


def profile_for(profiles, key):
    result = default_profile(key)
    if key.startswith("checklist:"):
        result.update(profiles.get("checklist", {}))
    result.update(profiles.get(key, {}))
    return result


def automation_profile(profiles, key):
    """Command Center defaults, preserving artwork without reviving the old dark theme."""
    result = default_profile("settings:" + key)
    result["title"] = None
    result.update({k: v for k, v in profiles.get(key, {}).items() if k != "style"})
    result.update({k: v for k, v in profiles.get("settings:general", {}).items() if k != "title"})
    result.update(profiles.get("settings:" + key, {}))
    return result


def automation_files(key, profile, names):
    profile = dict(profile or automation_profile({}, key))
    profile["title"] = "GENERAL SETTINGS 𖥸 " + (profile["title"] or key.upper())
    profile["_rotated_divider"] = True
    return menu_files(profile, profile["title"], key.upper() + " SETTINGS", names)


def decode(value):
    return base64.b64decode(value) if value else None


def normalized_upload(data, *, icon=False):
    image = read_upload(data, icon=icon)
    image.thumbnail((1600, 1000), Image.Resampling.LANCZOS)
    return base64.b64encode(png(image)).decode("ascii")


def render_menu(profile, title):
    style, height = profile["style"], profile["height"]
    header = render_header(
        decode(profile["header_background"]) or png(floral_background(style, (1600, height))),
        style,
        framed=profile["framed"],
        height=height,
        title=profile["title"] or title,
        rotated_divider=profile.get("_rotated_divider", False),
    )
    size = (1600, 150)
    background = decode(profile["footer_background"])
    footer = (
        ImageOps.fit(read_upload(background), size, Image.Resampling.LANCZOS)
        if background
        else floral_background(style, size)
    )
    if profile["footer_text"]:
        mask = text_mask(
            profile["footer_text"], role="display", size=52, min_size=24, max_width=1000
        )
        ink = Image.new("RGBA", mask.size, COLOURS[style])
        ink.putalpha(mask)
        footer.alpha_composite(ink, ((1600 - mask.width) // 2, (150 - mask.height) // 2))
    elif profile["footer_icon"]:
        icon = ImageOps.contain(
            read_upload(decode(profile["footer_icon"]), icon=True),
            (85, 85),
            Image.Resampling.LANCZOS,
        )
        footer.alpha_composite(icon, ((1600 - icon.width) // 2, (150 - icon.height) // 2))
    else:
        footer.alpha_composite(centered_overlay(style, size))
    if profile["framed"]:
        footer.alpha_composite(rectangular_frame(style, size, radius=8, inset=10))
    return header, png(footer)


def menu_files(profile, title, heading, names):
    header, footer = render_menu(profile, title)
    return [
        discord.File(io.BytesIO(data), filename=name)
        for name, data in zip(names, (header, footer, title_image(heading, profile["style"])))
    ]


def board_with_profile(board, profile):
    result = dict(
        board,
        style=profile["style"],
        height=profile["height"],
        name=profile["title"] or board["name"],
        footer_text=profile["footer_text"],
    )
    for key in ("header_background", "footer_background", "footer_icon"):
        result[key] = decode(profile[key])
    result["_menu_profile"] = profile
    return result
