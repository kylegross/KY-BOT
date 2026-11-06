"""Validated custom content and Discord-native rendering."""

import base64
import io
import re
import warnings

import discord
from PIL import Image, ImageOps, UnidentifiedImageError

STYLES = {
    "bold": "Bold",
    "italic": "Italic",
    "bold_italic": "Bold italic",
    "underline": "Underline",
    "strike": "Strikethrough",
    "spoiler": "Spoiler",
    "heading": "Heading",
    "quote": "Quote",
    "code": "Monospace",
    "special": "Special lettering · 𝙆𝙔 𝘽𝙊𝙏",
}
FIELD_STYLES = {
    "plain": "Plain text",
    "large": "Large heading",
    "decorative": "Decorative heading · KY BOT font",
    **STYLES,
}
MAX_SECTIONS = 5
PRESETS = {
    "BDC3CA": "Dark silver",
    "E8C47C": "Gold",
    "39DCCB": "Teal",
    "5865F2": "Indigo",
    "E989BA": "Rose",
    "57F287": "Green",
}


def defaults():
    return dict(
        name="Untitled embed",
        title="",
        title_style="large",
        body_style="plain",
        footer_style="plain",
        body="",
        footer_text="",
        color="BDC3CA",
        header=None,
        header_title="",
        header_finish="dark_silver",
        footer=None,
        channel=0,
        sticky_every=0,
        text_color=None,
        text_finish="dark_silver",
        sections=[],
    )


def color(value):
    value = str(value).strip().removeprefix("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", value):
        raise ValueError("Enter a six-digit color such as #39DCCB or #BDC3CA.")
    return value


def field_text(data, key):
    value = data[key]
    if not value:
        return ""
    style_key = {"footer_text": "footer_style"}.get(key, key + "_style")
    style = data.get(style_key, "large" if key == "title" else "plain")
    return value if style == "decorative" else styled(value, style)


def text_content(data):
    parts = []
    if data["title"]:
        parts.append(field_text(data, "title"))
    if data["body"]:
        parts.append(field_text(data, "body"))
    return "\n\n".join(parts)


def validate(data, *, publish=False):
    data = defaults() | data
    limits = {"name": 64, "title": 200, "body": 3500, "footer_text": 200, "header_title": 80}
    for key, maximum in limits.items():
        if not isinstance(data[key], str) or len(data[key]) > maximum:
            raise ValueError(f"{key.replace('_', ' ').title()} must be under {maximum} characters.")
    data["name"] = data["name"].strip()
    if not data["name"]:
        raise ValueError("Give this saved embed a name.")
    if "\n" in data["title"]:
        raise ValueError("Keep the title on one line; use the message for multiple lines.")
    for key in ("title_style", "body_style", "footer_style"):
        if data[key] not in FIELD_STYLES:
            raise ValueError("Choose a valid text style.")
    from ky_bot.design.menu_design import FINISHES
    from ky_bot.design.text_colour import text_colour

    if data["header_finish"] not in FINISHES:
        raise ValueError("Choose a valid header finish.")
    if data["header_title"]:
        heading_text(data["header_title"])
    data["text_color"] = text_colour(data["text_color"])
    if data["text_finish"] not in FINISHES:
        raise ValueError("Choose a decorative text finish.")
    for key, style_key in (
        ("title", "title_style"),
        ("body", "body_style"),
        ("footer_text", "footer_style"),
    ):
        if data[style_key] == "decorative" and data[key]:
            heading_text(data[key])
    sections = data["sections"]
    if not isinstance(sections, list) or len(sections) > MAX_SECTIONS:
        raise ValueError(f"Use up to {MAX_SECTIONS} sections per embed.")
    data["sections"] = []
    for section in sections:
        if not isinstance(section, dict):
            raise ValueError("Each section needs a heading and message.")
        heading = section.get("heading", "")
        body = section.get("body", "")
        heading_text(heading)
        if not isinstance(body, str) or len(body) > 1500:
            raise ValueError("Keep each section message under 1,500 characters.")
        data["sections"].append(dict(heading=heading, body=body))
    data["color"] = color(data["color"])
    if type(data["channel"]) is not int or data["channel"] < 0:
        raise ValueError("Choose a destination channel.")
    every = data["sticky_every"]
    if type(every) is not int or not 0 <= every <= 10000:
        raise ValueError("Use 0 for no sticky, or a message count from 1 to 10,000.")
    section_length = sum(len(s["heading"]) + len(s["body"]) + 4 for s in data["sections"])
    if len(text_content(data)) + len(field_text(data, "footer_text")) + section_length + 3 > 4000:
        raise ValueError("Keep the complete embed under 4,000 characters.")
    if publish:
        if not data["channel"]:
            raise ValueError("Choose a destination channel first.")
        if not data["sections"] and not any(
            data[k] and str(data[k]).strip()
            for k in ("title", "body", "footer_text", "header", "footer")
        ):
            raise ValueError("Add some text or an image before previewing or publishing.")
    return data


def heading_text(value):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 80
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("Use a single-line decorative heading with 1–80 characters.")


def styled(text, style):
    if not text.strip():
        raise ValueError("Enter some text to format.")
    if style == "plain":
        return text
    if style == "large":
        return "\n".join("# " + line for line in text.splitlines())
    if style == "special":
        mapping = {ord(chr(65 + i)): chr(0x1D63C + i) for i in range(26)}
        mapping.update({ord(chr(97 + i)): chr(0x1D656 + i) for i in range(26)})
        return text.translate(mapping)
    wrappers = {
        "bold": "**",
        "italic": "*",
        "bold_italic": "***",
        "underline": "__",
        "strike": "~~",
        "spoiler": "||",
        "code": "`",
    }
    if style in wrappers:
        marker = wrappers[style]
        return marker + text + marker
    if style in {"heading", "quote"}:
        prefix = "## " if style == "heading" else "> "
        return "\n".join(prefix + line for line in text.splitlines())
    raise ValueError("Choose a formatting style.")


def image_upload(raw):
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Upload an image under 8 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.width * source.height > 24000000:
                    raise ValueError("Use an image under 24 megapixels.")
                image = ImageOps.exif_transpose(source).convert("RGBA")
                image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                out = io.BytesIO()
                image.save(out, format="PNG", optimize=True)
    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise ValueError("Upload a valid PNG, JPEG, GIF or WebP image.") from None
    result = out.getvalue()
    if len(result) > 4 * 1024 * 1024:
        raise ValueError("The image is too large after processing; use a smaller image.")
    return base64.b64encode(result).decode("ascii")


def render(data):
    data = validate(data)
    view = discord.ui.LayoutView(timeout=None)
    panel = discord.ui.Container(accent_colour=int(data["color"], 16))
    attachments = []
    for key in ("header", "footer"):
        if key == "footer":

            def decorative(text, name):
                from ky_bot.checklists.artwork import title_image

                image = title_image(text, data["text_finish"], data["text_color"])
                attachments.append(discord.File(io.BytesIO(image), filename=name))
                panel.add_item(
                    discord.ui.MediaGallery(
                        discord.MediaGalleryItem("attachment://" + name, description=text)
                    )
                )

            # Keep the original combined text block for existing drafts.
            if "decorative" not in (data["title_style"], data["body_style"]):
                content = text_content(data)
                if content:
                    panel.add_item(discord.ui.TextDisplay(content))
            else:
                for field in ("title", "body"):
                    if data[field]:
                        if data[field + "_style"] == "decorative":
                            decorative(data[field], "custom_" + field + ".png")
                        else:
                            panel.add_item(discord.ui.TextDisplay(field_text(data, field)))
            for index, section in enumerate(data["sections"]):
                panel.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
                decorative(section["heading"], f"custom_section_{index}.png")
                if section["body"].strip():
                    panel.add_item(discord.ui.TextDisplay(section["body"]))
            if data["footer_text"]:
                if data["footer_style"] == "decorative":
                    decorative(data["footer_text"], "custom_footer_text.png")
                else:
                    prefix = "-# " if data["footer_style"] == "plain" else ""
                    panel.add_item(discord.ui.TextDisplay(prefix + field_text(data, "footer_text")))
        if data[key]:
            name = "custom_" + key + ".png"
            raw = base64.b64decode(data[key])
            if key == "header" and data["header_title"]:
                from ky_bot.design.headers import render_header
                from ky_bot.design.menu_design import FINISHES

                from .artwork import standard_image

                finish = next(
                    (key for key in FINISHES if data["header"] == standard_image("header", key)),
                    data["header_finish"],
                )
                raw = render_header(raw, finish, height=280, title=data["header_title"])
            attachments.append(discord.File(io.BytesIO(raw), filename=name))
            panel.add_item(
                discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://" + name))
            )
    if not panel.children:
        panel.add_item(discord.ui.TextDisplay("Add text or an image to this draft."))
    view.add_item(panel)
    return view, attachments
