"""Per-channel artwork and decorative lettering; never mutates shared design profiles."""

import io
from functools import lru_cache

from PIL import Image, ImageDraw, ImageOps

from ky_bot.checklists.artwork import png
from ky_bot.design.collection import COLOURS, floral_background
from ky_bot.design.footers import centered_overlay, rectangular_frame, text_ink
from ky_bot.design.headers import asset, render_header

from .headings import DIVIDER, lettering


def banner_mask(title):
    parts = title.split(DIVIDER, 1)
    if len(parts) == 1:
        return ImageOps.contain(lettering(title), (1280, 40), Image.Resampling.LANCZOS)
    glyphs = [lettering(part) for part in parts]
    lily = asset("ky_divider_bamum.png").getchannel("A").transpose(Image.Transpose.ROTATE_180)
    lily = lily.crop(lily.getbbox())
    glyphs.insert(1, lily)
    scaled = [
        glyph.resize((max(1, round(glyph.width * 40 / glyph.height)), 40), Image.Resampling.LANCZOS)
        for glyph in glyphs
    ]
    mask = Image.new("L", (sum(glyph.width for glyph in scaled) + 36, 40))
    left = 0
    for glyph in scaled:
        mask.paste(glyph, (left, 0))
        left += glyph.width + 18
    return ImageOps.contain(mask, (1280, 40), Image.Resampling.LANCZOS)


def banner_title(title, text_finish, text_color):
    mask = banner_mask(title)
    layer = Image.new("RGBA", (1600, 260))
    left, top = (1600 - mask.width) // 2, (260 - mask.height) // 2
    layer.alpha_composite(text_ink(mask, text_finish, text_color), (left, top))
    pen = ImageDraw.Draw(layer)
    for y in (top - 14, top + mask.height + 14):
        pen.line(
            (left - 10, y, left + mask.width + 10, y),
            fill=text_color or COLOURS[text_finish],
            width=2,
        )
    return layer


@lru_cache(maxsize=128)
def standard_artwork(kind, finish, title, text_finish, text_color):
    if kind == "footer":
        image = floral_background(finish, (1600, 150))
        image.alpha_composite(centered_overlay(finish, image.size))
        image.alpha_composite(rectangular_frame(finish, image.size))
        return png(image)
    raw = render_header(
        png(floral_background(finish, (1600, 260))),
        finish,
        framed=True,
        height=260,
        dim=35,
        title=None,
    )
    image = Image.open(io.BytesIO(raw)).convert("RGBA")
    image.alpha_composite(banner_title(title, text_finish, text_color))
    return png(image)
