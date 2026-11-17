"""Metallic lettering over generated artwork or server-owned uploaded images."""

import io

from PIL import Image, ImageDraw


def metallic_ink(mask, style, color=None):
    from .footers import metal
    from .metallic import using_palette
    from .text_colour import text_colour

    color = text_colour(color)
    if color:
        with using_palette({"base": color}):
            return metal(mask, "dark_silver")
    return metal(mask, style)


def add_image_text(raw, text, kind, style="silver_neon", color=None):
    """Keep uploaded pixels intact except for the explicitly requested text overlay."""
    if not text:
        return raw
    from .collection import COLOURS
    from .typography import text_mask

    with Image.open(io.BytesIO(raw)) as source:
        image = source.convert("RGBA")
    width, height = image.size
    size = max(12, min(78, round(height * (0.2 if kind == "header" else 0.35))))
    mask = text_mask(
        text, role="display", size=size, min_size=min(12, size), max_width=max(1, width - 48)
    )
    left, top = (width - mask.width) // 2, (height - mask.height) // 2
    image.alpha_composite(metallic_ink(mask, style, color), (left, top))
    if kind == "header":
        pen = ImageDraw.Draw(image)
        gap = max(3, min(14, height // 24))
        for y in (top - gap, top + mask.height + gap):
            pen.line((left, y, left + mask.width, y), fill=color or COLOURS[style], width=1)
    output = io.BytesIO()
    image.save(output, "PNG")
    return output.getvalue()
