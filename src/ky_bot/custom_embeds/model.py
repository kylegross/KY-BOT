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
        body="",
        footer_text="",
        color="BDC3CA",
        header=None,
        footer=None,
        channel=0,
        sticky_every=0,
    )


def color(value):
    value = str(value).strip().removeprefix("#").upper()
    if not re.fullmatch(r"[0-9A-F]{6}", value):
        raise ValueError("Enter a six-digit color such as #39DCCB or #BDC3CA.")
    return value


def text_content(data):
    parts = []
    if data["title"]:
        parts.append("## " + data["title"])
    if data["body"]:
        parts.append(data["body"])
    return "\n\n".join(parts)


def validate(data, *, publish=False):
    data = defaults() | data
    limits = {"name": 64, "title": 200, "body": 3500, "footer_text": 200}
    for key, maximum in limits.items():
        if not isinstance(data[key], str) or len(data[key]) > maximum:
            raise ValueError(f"{key.replace('_', ' ').title()} must be under {maximum} characters.")
    data["name"] = data["name"].strip()
    if not data["name"]:
        raise ValueError("Give this saved embed a name.")
    if "\n" in data["title"]:
        raise ValueError("Keep the title on one line; use the message for multiple lines.")
    data["color"] = color(data["color"])
    if type(data["channel"]) is not int or data["channel"] < 0:
        raise ValueError("Choose a destination channel.")
    every = data["sticky_every"]
    if type(every) is not int or not 0 <= every <= 10000:
        raise ValueError("Use 0 for no sticky, or a message count from 1 to 10,000.")
    if len(text_content(data)) + len(data["footer_text"]) + 3 > 4000:
        raise ValueError("Keep the complete embed under 4,000 characters.")
    if publish:
        if not data["channel"]:
            raise ValueError("Choose a destination channel first.")
        if not any(
            data[k] and str(data[k]).strip()
            for k in ("title", "body", "footer_text", "header", "footer")
        ):
            raise ValueError("Add some text or an image before previewing or publishing.")
    return data


def styled(text, style):
    if not text.strip():
        raise ValueError("Enter some text to format.")
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
            content = text_content(data)
            if content:
                panel.add_item(discord.ui.TextDisplay(content))
            if data["footer_text"]:
                panel.add_item(discord.ui.TextDisplay("-# " + data["footer_text"]))
        if data[key]:
            name = "custom_" + key + ".png"
            attachments.append(discord.File(io.BytesIO(base64.b64decode(data[key])), filename=name))
            panel.add_item(
                discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://" + name))
            )
    if not panel.children:
        panel.add_item(discord.ui.TextDisplay("Add text or an image to this draft."))
    view.add_item(panel)
    return view, attachments
