"""Slogan-free KY BOT headers using reusable, privately rendered brand layers."""

import io
from importlib.resources import files
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

from ky_bot.services.footers import FooterError, read_upload
from ky_bot.services.typography import TypographyError, text_mask

SIZE = (1600, 520)
STYLES = ("gold", "silver_neon", "dark_silver")
DESIGNS = ("volcanic", "botanical", "abstract_nature", "floral_wreath", "stone_minimal")


def asset(name: str) -> Image.Image:
    with files("ky_bot").joinpath("assets", "header", name).open("rb") as stream:
        return Image.open(stream).convert("RGBA")


def background_mask(*, framed: bool = True) -> Image.Image:
    mask = Image.new("L", SIZE)
    box = (28, 24, 1571, 495) if framed else (0, 0, 1599, 519)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=100 if framed else 32, fill=255)
    return mask


def overlay(style: str, *, framed: bool = True) -> Image.Image:
    if style not in STYLES:
        raise FooterError("Choose metallic gold, iridescent silver, or dark metallic silver.")
    result = asset(f"{style}_brand.png")
    if framed:
        # The brand sits above the frame, within the top-left interior safe area.
        frame = asset(f"{style}_frame.png")
        frame.alpha_composite(result.crop((100, 68, 371, 149)), (72, 68))
        return frame
    # Borderless layouts can place the mark closer to the actual image corner.
    corner = Image.new("RGBA", SIZE)
    corner.alpha_composite(result.crop((100, 68, 371, 149)), (24, 24))
    return corner


def render_header(
    background: bytes,
    style: str = "gold",
    *,
    framed: bool = True,
    crop_x: int = 50,
    crop_y: int = 50,
    dim: int = 0,
    title: str | None = None,
    font_path: Path | None = None,
) -> bytes:
    if not all(0 <= value <= 100 for value in (crop_x, crop_y)) or not 0 <= dim <= 80:
        raise FooterError("Crop positions must be 0–100 and dimming must be 0–80.")
    image = ImageOps.fit(
        read_upload(background),
        SIZE,
        Image.Resampling.LANCZOS,
        centering=(crop_x / 100, crop_y / 100),
    )
    image.alpha_composite(Image.new("RGBA", SIZE, (0, 0, 0, round(255 * dim / 100))))
    image.putalpha(ImageChops.multiply(image.getchannel("A"), background_mask(framed=framed)))
    image.alpha_composite(overlay(style, framed=framed))
    if title is not None:
        try:
            mask = text_mask(
                title,
                role="display",
                font_path=font_path,
                size=108,
                min_size=48,
                max_width=1280,
            )
        except TypographyError as exc:
            raise FooterError(str(exc)) from None
        # Center the title between two fine rules, following the KY BOT logo treatment.
        x, y = (SIZE[0] - mask.width) // 2, 252
        rule_width = max(240, mask.width)
        rule_left = (SIZE[0] - rule_width) // 2
        top, bottom = y - 28, y + mask.height + 28
        dark = style == "dark_silver"
        colour = "#292724" if dark else "#f5e9ce" if style == "gold" else "#edf1f5"
        # A feathered contrast area retains the background texture without a boxed title.
        shade = Image.new("L", SIZE)
        ImageDraw.Draw(shade).rounded_rectangle(
            (rule_left - 48, top - 30, rule_left + rule_width + 48, bottom + 30),
            radius=35,
            fill=165,
        )
        veil = Image.new("RGBA", SIZE, "#f6f0e3" if dark else "#080a0c")
        veil.putalpha(shade.filter(ImageFilter.GaussianBlur(28)))
        image.alpha_composite(veil)
        pen = ImageDraw.Draw(image)
        for rule_y in (top, bottom):
            pen.line((rule_left, rule_y, rule_left + rule_width, rule_y), fill=colour, width=3)
        text = Image.new("RGBA", mask.size, colour)
        text.putalpha(mask)
        image.alpha_composite(text, (x, y))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def design_file(name: str, *, framed: bool = True) -> bytes:
    """Reusable preset for embedding with attachment://ky_header.png."""
    if name not in DESIGNS:
        raise FooterError("Choose a design from the KY BOT header collection.")
    suffix = "" if framed else "_borderless"
    return (
        files("ky_bot").joinpath("assets", "header", f"ky_header_{name}{suffix}.png").read_bytes()
    )
