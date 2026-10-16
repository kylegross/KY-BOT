"""KY BOT typography for rendered graphics; licensed fonts remain external assets."""

import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

TAGLINE = "POWERFUL BY NATURE. SIMPLE BY DESIGN."
ROLES = {
    "display": ("Bold", 64, 0.01),
    "heading": ("Medium", 36, 0.02),
    "subheading": ("Regular", 24, 0.01),
    "body": ("Regular", 18, 0.0),
    "label": ("Regular", 14, 0.06),
    "tagline": ("Light", 18, 0.08),
}


class TypographyError(ValueError):
    """A font or text configuration that cannot produce branded lettering."""


def section_heading_mask(text: str) -> Image.Image:
    """Keep section metrics unchanged while strengthening small displayed edges.

    Sharpen coverage only, so transparent lettering cannot acquire colored halos.
    All finishes and menus use the same treatment before Discord scales the image.
    """
    mask = text_mask(text, role="display", size=36, min_size=36, max_width=1080)
    return mask.filter(ImageFilter.UnsharpMask(radius=1.2, percent=220, threshold=2))


def font_file(role: str) -> Path:
    style = ROLES[role][0]
    configured = os.getenv("KY_BOT_FONT_DIR", "").strip()
    directory = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parents[3] / "design/assets/branding/fonts/private"
    )
    for suffix in ("otf", "ttf"):
        candidate = directory / f"Vonca-{style}.{suffix}"
        if candidate.is_file():
            return candidate
    raise TypographyError(f"Install the licensed VONCA {style} font for this graphic.")


def divided_title_mask(text, divider, *, size, min_size, max_width, font_path=None):
    """Set upright VONCA text around an independently rotated ornamental divider."""
    left, right = text.split(" 𖥸 ", 1)
    for pixels in range(size, min_size - 1, -1):
        parts = [text_mask(part, role="display", font_path=font_path, size=pixels,
                           min_size=pixels, max_width=10000) for part in (left, right)]
        height = max(part.height for part in parts)
        ornament = divider.convert("L").transpose(Image.Transpose.ROTATE_180)
        ornament = ornament.resize(
            (max(1, round(ornament.width * height / ornament.height)), height),
            Image.Resampling.LANCZOS,
        )
        gap = round(pixels * 0.35)
        width = sum(part.width for part in parts) + ornament.width + gap * 2
        if width <= max_width:
            result = Image.new("L", (width, height))
            x = 0
            for part in (parts[0], ornament, parts[1]):
                result.paste(part, (x, (height - part.height) // 2))
                x += part.width + gap
            return result
    raise TypographyError("That slogan is too wide. Please shorten it.")


def text_mask(
    text: str,
    *,
    role: str = "tagline",
    font_path: Path | None = None,
    size: int = 44,
    max_width: int = 1000,
    min_size: int = 20,
) -> Image.Image:
    """Fit one line with real font metrics, kerning, and role-specific tracking."""
    if not text or len(text) > 80 or any(ord(c) < 32 for c in text):
        raise TypographyError("Use a single-line slogan with 1–80 characters.")
    path = font_path if font_path is not None else font_file(role)
    tracking = ROLES[role][2]
    try:
        for pixels in range(size, min_size - 1, -1):
            font = ImageFont.truetype(str(path), pixels)
            positions = [0.0]
            for previous, current in zip(text, text[1:]):
                advance = font.getlength(previous + current) - font.getlength(current)
                positions.append(positions[-1] + advance + pixels * tracking)
            boxes = [font.getbbox(char) for char in text]
            left = min(x + box[0] for x, box in zip(positions, boxes))
            right = max(x + box[2] for x, box in zip(positions, boxes))
            top = min(box[1] for box in boxes)
            bottom = max(box[3] for box in boxes)
            if right - left <= max_width:
                break
        else:
            raise TypographyError("That slogan is too wide. Please shorten it.")
    except OSError:
        raise TypographyError("The configured Vonca font could not be loaded.") from None
    mask = Image.new("L", (max(1, round(right - left) + 2), max(1, bottom - top + 2)))
    draw = ImageDraw.Draw(mask)
    for char, x in zip(text, positions):
        draw.text((x - left, -top), char, font=font, fill=255)
    return mask
