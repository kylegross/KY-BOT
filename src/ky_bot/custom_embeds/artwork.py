"""Persistent standard artwork choices for custom embeds."""

import base64
import io
from functools import lru_cache

from PIL import Image

from ky_bot.design.collection import floral_background
from ky_bot.design.footers import centered_overlay, rectangular_frame
from ky_bot.design.headers import design_file
from ky_bot.design.menu_design import FINISHES


@lru_cache(maxsize=8)
def standard_image(kind, finish):
    if kind not in {"header", "footer"} or finish not in FINISHES:
        raise ValueError("Choose a standard header or footer finish.")
    if kind == "header":
        raw = design_file(finish, compact=True)
    else:
        image = floral_background(finish, (1600, 150))
        image.alpha_composite(centered_overlay(finish, image.size))
        image.alpha_composite(rectangular_frame(finish, image.size))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        raw = stream.getvalue()
    # Store actual PNG bytes like uploads, so publishing and sticky reposts work offline.
    image = Image.open(io.BytesIO(raw)).convert("RGBA")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")


def custom_image(kind, palette):
    from ky_bot.design.metallic import tint, using_palette

    with using_palette(palette):
        if kind == "header":
            image = tint(
                Image.open(io.BytesIO(design_file("dark_silver", compact=True))).convert("RGBA")
            )
        elif kind == "footer":
            image = floral_background("dark_silver", (1600, 150))
            image.alpha_composite(centered_overlay("dark_silver", image.size))
            image.alpha_composite(rectangular_frame("dark_silver", image.size))
        else:
            raise ValueError("Choose a header or footer.")
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode("ascii")
