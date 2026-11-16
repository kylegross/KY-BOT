"""Custom metallic palettes scoped to a render; original finishes stay unchanged."""

import colorsys
import math
import re
from contextlib import contextmanager
from contextvars import ContextVar

from PIL import Image, ImageChops, ImageColor, ImageOps

_CURRENT = ContextVar("ky_metallic_palette", default=None)


def parse_palette(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) - {"base", "highlight", "shadow", "accent"}:
        raise ValueError("Choose a base color and optional highlight, shadow and accent colors.")
    result = {}
    for key in ("base", "highlight", "shadow", "accent"):
        text = str(value.get(key) or "").strip().removeprefix("#")
        if not text and key != "base":
            result[key] = None
        elif re.fullmatch(r"[0-9a-fA-F]{6}", text):
            result[key] = "#" + text.upper()
        else:
            raise ValueError("Use six-digit hex colors, such as #7B2CBF. Base color is required.")
    return result


def resolved_palette(value):
    raw = parse_palette(value)
    if raw is None:
        return None
    base = ImageColor.getrgb(raw["base"])
    hue, saturation, brightness = colorsys.rgb_to_hsv(*(part / 255 for part in base))

    def rgb(h, s, v):
        return tuple(round(c * 255) for c in colorsys.hsv_to_rgb(h % 1, s, v))

    return dict(
        base=base,
        shadow=ImageColor.getrgb(raw["shadow"])
        if raw["shadow"]
        else tuple(round(c * 0.16) for c in base),
        highlight=ImageColor.getrgb(raw["highlight"])
        if raw["highlight"]
        else tuple(round(c * 0.18 + 255 * 0.82) for c in base),
        accent=ImageColor.getrgb(raw["accent"])
        if raw["accent"]
        else rgb(hue + 0.13, max(0.25, saturation * 0.75), max(0.85, brightness)),
    )


@contextmanager
def using_palette(value):
    token = _CURRENT.set(resolved_palette(value))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def active_palette():
    return _CURRENT.get()


def base_color(fallback):
    palette = active_palette()
    return palette["base"] if palette else fallback


def tint(image):
    """Retain the original detail/luminance and alpha, with palette-based reflections."""
    palette = active_palette()
    if palette is None:
        return image
    source = image.convert("RGBA")
    light = ImageOps.grayscale(source)
    stops = [
        (0, (0, 0, 0)),
        (55, palette["shadow"]),
        (110, palette["base"]),
        (210, palette["highlight"]),
        (255, palette["highlight"]),
    ]
    tables = [[], [], []]
    for value in range(256):
        for (start, low), (end, high) in zip(stops, stops[1:]):
            if start <= value <= end:
                amount = (value - start) / (end - start)
                for channel in range(3):
                    tables[channel].append(
                        round(low[channel] + (high[channel] - low[channel]) * amount)
                    )
                break
    colored = Image.merge("RGB", tuple(light.point(table) for table in tables))
    # Bright, colored reflections alternate across the surface; unlike a flat
    # tint, their palette reaches the selected highlight instead of fading black.
    ramp = Image.new("L", (source.width, 1))
    ramp.putdata(
        [
            round(155 * (0.5 + 0.5 * math.sin(x / max(1, source.width - 1) * 12.566)))
            for x in range(source.width)
        ]
    )
    material = light.point(lambda v: min(255, max(0, (v - 45) * 2)))
    # Leave deep background texture alone and retain bright specular edges.
    reflection = light.point(lambda v: max(0, min(255, (v - 45) * 3, (235 - v) * 4)))
    strength = ImageChops.multiply(reflection, ramp.resize(source.size))
    accent = ImageOps.colorize(
        light, palette["shadow"], palette["highlight"], mid=palette["accent"], midpoint=150
    )
    colored = Image.composite(accent, colored, strength)
    # Narrow polished highlights plus a quiet reflected shadow add chrome depth.
    shine, shade = Image.new("L", (1, source.height)), Image.new("L", (1, source.height))
    sheen_values, shadow_values = [], []
    for y in range(source.height):
        position = y / max(1, source.height - 1)
        sheen_values.append(
            round(
                145 * math.exp(-(((position - 0.27) / 0.045) ** 2))
                + 115 * math.exp(-(((position - 0.68) / 0.055) ** 2))
            )
        )
        shadow_values.append(round(48 * math.exp(-(((position - 0.46) / 0.07) ** 2))))
    shine.putdata(sheen_values)
    shade.putdata(shadow_values)
    highlight_mask = ImageChops.multiply(material, shine.resize(source.size))
    shadow_mask = ImageChops.multiply(material, shade.resize(source.size))
    colored = Image.composite(
        Image.new("RGB", source.size, palette["shadow"]), colored, shadow_mask
    )
    colored = Image.composite(
        Image.new("RGB", source.size, palette["highlight"]), colored, highlight_mask
    )
    colored.putalpha(source.getchannel("A"))
    return colored
