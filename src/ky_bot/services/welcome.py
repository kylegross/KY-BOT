"""Personalized welcome cards rendered as one image with a server-owned background."""

import io
import re
from importlib.resources import files

from PIL import Image, ImageDraw, ImageFont, ImageOps

from ky_bot.database.settings import WelcomeArtwork
from ky_bot.services.footers import read_upload
from ky_bot.services.typography import TypographyError, font_file

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
        return ImageFont.load_default(size=size)


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
        or files("ky_bot").joinpath("assets", "welcome", "ky_silver_flowers.png").read_bytes()
    )
    image = ImageOps.fit(read_upload(background), SIZE, Image.Resampling.LANCZOS)
    image.alpha_composite(Image.new("RGBA", SIZE, (0, 0, 0, round(255 * artwork.dim / 100))))
    brand = files("ky_bot").joinpath("assets", "header", "silver_neon_brand.png").read_bytes()
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
        draw.text(
            (600, 171),
            readable(member_name)[:1].upper() or "?",
            font=font("display", 80),
            fill=accent,
            anchor="mm",
        )

    def centered(text, y, role, size, colour):
        text = readable(text)
        chosen = font(role, size)
        while draw.textlength(text, font=chosen) > 1040 and size > 18:
            size -= 2
            chosen = font(role, size)
        draw.text((600, y), text, font=chosen, fill=colour, anchor="mt")

    centered(artwork.title.upper(), 320, "display", 70, accent)
    centered(member_name, 415, "heading", 44, "#F5F1E8")
    centered(server_name, 478, "body", 28, accent)
    body_font = font("body", 30)
    # Bound the visual copy; the full configured message remains in the accessible caption.
    body = readable(message)
    lines = []
    line = ""
    for word in body.split():
        if draw.textlength((line + " " + word).strip(), font=body_font) > 980:
            if line:
                lines.append(line)
                line = ""
            while draw.textlength(word, font=body_font) > 980:
                split = len(word) // 2
                while draw.textlength(word[:split], font=body_font) > 980:
                    split -= 1
                lines.append(word[:split])
                word = word[split:]
        line = (line + " " + word).strip()
    if line:
        lines.append(line)
    if len(lines) > 5:
        lines = lines[:5]
        lines[-1] = lines[-1][:-3] + "…"
    for index, line in enumerate(lines):
        draw.text((600, 550 + index * 38), line, font=body_font, fill="#E9E6DF", anchor="mt")
    output = io.BytesIO()
    image.convert("RGB").save(output, format="PNG")
    return output.getvalue()
