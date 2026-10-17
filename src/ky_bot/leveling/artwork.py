"""Wide silver level-up card, sharing the welcome artwork and VONCA lettering."""

import io
from importlib.resources import files

from PIL import Image, ImageDraw, ImageOps

from ky_bot.checklists.artwork import png
from ky_bot.design.footers import read_upload
from ky_bot.design.headers import asset
from ky_bot.design.welcome import font, readable


def level_card(name, level, date, avatar=None):
    size = (1440, 480)
    background = (
        files("ky_bot.design").joinpath("assets", "welcome", "ky_silver_flowers.png").read_bytes()
    )
    image = ImageOps.fit(read_upload(background), size, Image.Resampling.LANCZOS)
    image.alpha_composite(Image.new("RGBA", size, (0, 0, 0, 65)))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((9, 9, 1430, 470), radius=18, outline="#D6E2F3", width=3)
    brand = asset("silver_neon_brand.png").crop((100, 68, 371, 149))
    image.alpha_composite(brand.resize((158, 47), Image.Resampling.LANCZOS), (28, 25))
    draw.text((1403, 30), date, font=font("body", 22), fill="#D6E2F3", anchor="rt")
    draw.ellipse((187, 121, 425, 359), outline="#D6E2F3", width=4)
    if avatar:
        try:
            portrait = ImageOps.fit(read_upload(avatar), (222, 222), Image.Resampling.LANCZOS)
            mask = Image.new("L", portrait.size)
            ImageDraw.Draw(mask).ellipse((0, 0, 221, 221), fill=255)
            portrait.putalpha(mask)
            image.alpha_composite(portrait, (195, 129))
        except (ValueError, OSError):
            avatar = None
    if not avatar:
        draw.ellipse((195, 129, 417, 351), fill="#172331")
        draw.text(
            (306, 240),
            readable(name)[:1] or "?",
            font=font("display", 90),
            fill="#D6E2F3",
            anchor="mm",
        )
    for text, y, pixels in (
        ("LEVEL UP", 128, 58),
        (readable(name), 214, 62),
        (f"LEVEL {level}", 308, 46),
    ):
        face = font("display", pixels)
        while draw.textlength(text, font=face) > 760 and pixels > 18:
            pixels -= 2
            face = font("display", pixels)
        draw.text((883, y), text, font=face, fill="#D6E2F3", anchor="mt")
    return png(image)


def card_file(data):
    import discord

    return discord.File(io.BytesIO(data), filename="ky_level_up.png")
