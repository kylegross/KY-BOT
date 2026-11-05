"""Wide silver level-up card, sharing the welcome artwork and VONCA lettering."""

import io
from importlib.resources import files

from PIL import Image, ImageDraw, ImageOps

from ky_bot.checklists.artwork import png
from ky_bot.design.footers import read_upload
from ky_bot.design.headers import asset
from ky_bot.design.typography import divided_title_mask
from ky_bot.design.unicode_text import first_grapheme, fit_text
from ky_bot.design.welcome import font, readable
from ky_bot.leveling.store import defaults


def announcement(config, member, level):
    return config.get("announcement_text", "{mention} just leveled up!").format(
        mention=member.mention, name=member.display_name, level=level
    )


def level_card(name, level, date, avatar=None, settings=None):
    config = defaults() | (settings or {})
    height = config["card_height"]
    size = (1440, height)
    background = (
        files("ky_bot.design").joinpath("assets", "welcome", "ky_silver_flowers.png").read_bytes()
    )
    image = ImageOps.fit(read_upload(background), size, Image.Resampling.LANCZOS)
    image.alpha_composite(Image.new("RGBA", size, (0, 0, 0, 65)))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((9, 9, 1430, height - 10), radius=18, outline="#D6E2F3", width=3)
    brand = asset("silver_neon_brand.png").crop((100, 68, 371, 149))
    image.alpha_composite(brand.resize((158, 47), Image.Resampling.LANCZOS), (28, 25))
    draw.text((1403, 30), date, font=font("body", 22), fill="#D6E2F3", anchor="rt")
    portrait_layer = Image.new("RGBA", (1440, 480))
    draw = ImageDraw.Draw(portrait_layer)
    draw.ellipse((187, 121, 425, 359), outline="#D6E2F3", width=4)
    if avatar:
        try:
            portrait = ImageOps.fit(read_upload(avatar), (222, 222), Image.Resampling.LANCZOS)
            mask = Image.new("L", portrait.size)
            ImageDraw.Draw(mask).ellipse((0, 0, 221, 221), fill=255)
            portrait.putalpha(mask)
            portrait_layer.alpha_composite(portrait, (195, 129))
        except (ValueError, OSError):
            avatar = None
    if not avatar:
        draw.ellipse((195, 129, 417, 351), fill="#172331")
        initial = fit_text(
            first_grapheme(readable(name)), 90, str(font("display", 90).path), "#D6E2F3", 180
        )
        portrait_layer.alpha_composite(
            initial, (306 - initial.width // 2, 240 - initial.height // 2)
        )
    image.alpha_composite(portrait_layer, (0, (height - 480) // 2))
    draw = ImageDraw.Draw(image)
    cy = height // 2 + 12
    title = divided_title_mask(
        config["card_title"] + " \U00016978 LEVEL " + str(level),
        asset("ky_divider_bamum.png").getchannel("A"),
        size=config["title_size"],
        min_size=24,
        max_width=830,
    )
    layer = Image.new("RGBA", title.size, "#D6E2F3")
    layer.putalpha(title)
    center = 830
    image.alpha_composite(layer, (center - title.width // 2, cy - 75))
    text = readable(name) or "Member"
    name_layer = fit_text(
        text,
        config["name_size"],
        str(font("display", config["name_size"]).path),
        "#D6E2F3",
        820,
        metallic="silver_neon",
    )
    image.alpha_composite(name_layer, (center - name_layer.width // 2, cy + 8))
    return png(image)


def card_file(data):
    import discord

    return discord.File(io.BytesIO(data), filename="ky_level_up.png")
