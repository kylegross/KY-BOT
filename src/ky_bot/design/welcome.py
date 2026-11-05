"""Personalized welcome cards rendered as one image with a server-owned background."""

import io
import re
from importlib.resources import files

from PIL import Image, ImageDraw, ImageFont, ImageOps

from ky_bot.admin_functions.settings_repository import WelcomeArtwork
from ky_bot.design.footers import read_upload
from ky_bot.design.typography import TypographyError, font_file
from ky_bot.design.unicode_text import first_grapheme, fit_text, wrap_text

SIZE = (1200, 800)


def normalize_background(data: bytes) -> bytes:
    image = ImageOps.fit(read_upload(data), SIZE, Image.Resampling.LANCZOS)
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def font(role: str, size: int):
    try:
        return ImageFont.truetype(str(font_file(role)), size)
    except TypographyError:
        # Personal names still work on hosts where the licensed fonts have not been installed.
        return ImageFont.truetype(
            str(files("ky_bot.design").joinpath("assets", "fonts", "NotoSans-Regular.ttf")), size
        )


def readable(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", " ", text).strip()


def render_welcome(
    artwork: WelcomeArtwork,
    *,
    member_name: str,
    server_name: str,
    message: str,
    avatar: bytes | None = None,
) -> bytes:
    background = (
        artwork.background
        or files("ky_bot.design")
        .joinpath("assets", "welcome", "ky_silver_flowers.png")
        .read_bytes()
    )
    image = ImageOps.fit(read_upload(background), SIZE, Image.Resampling.LANCZOS)
    image.alpha_composite(Image.new("RGBA", SIZE, (0, 0, 0, round(255 * artwork.dim / 100))))
    brand = (
        files("ky_bot.design").joinpath("assets", "header", "silver_neon_brand.png").read_bytes()
    )
    logo = Image.open(io.BytesIO(brand)).convert("RGBA").crop((100, 68, 371, 149))
    logo = logo.resize((190, 57), Image.Resampling.LANCZOS)
    image.alpha_composite(logo, (24, 24))
    draw = ImageDraw.Draw(image)
    accent = "#" + artwork.accent
    draw.ellipse((494, 65, 706, 277), outline=accent, width=4)
    if avatar:
        try:
            portrait = ImageOps.fit(read_upload(avatar), (194, 194), Image.Resampling.LANCZOS)
            mask = Image.new("L", portrait.size)
            ImageDraw.Draw(mask).ellipse((0, 0, 193, 193), fill=255)
            portrait.putalpha(mask)
            image.alpha_composite(portrait, (503, 74))
        except ValueError:
            avatar = None
    if not avatar:
        draw.ellipse((503, 74, 697, 268), fill="#172331")
        initial = fit_text(
            first_grapheme(readable(member_name)), 80, str(font("display", 80).path), accent, 160
        )
        image.alpha_composite(initial, (600 - initial.width // 2, 171 - initial.height // 2))

    def centered(text, y, role, size, colour):
        text = readable(text)
        if not text:
            return
        tile = fit_text(text, size, str(font(role, size).path), colour, 1040)
        image.alpha_composite(tile, (600 - tile.width // 2, y))

    centered(artwork.title.upper(), 320, "display", 70, accent)
    centered(member_name, 415, "display", 68, "#F5F1E8")
    centered(server_name, 506, "body", 28, accent)
    # Preserve whole graphemes when wrapping names or emoji in the welcome message.
    primary = str(font("body", 30).path)
    lines = wrap_text(readable(message), 30, primary, 980, max_lines=5)
    for index, line in enumerate(lines):
        tile = fit_text(line, 30, primary, "#E9E6DF", 980)
        image.alpha_composite(tile, (600 - tile.width // 2, 574 + index * 38))
    output = io.BytesIO()
    image.convert("RGB").save(output, format="PNG")
    return output.getvalue()
