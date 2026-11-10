"""Public panel banners using the shared KY BOT designs or validated uploads."""

import base64
import io
from functools import lru_cache

import discord
from PIL import Image

from ky_bot.design.footers import read_upload
from ky_bot.design.menu_design import default_profile, render_menu


def normalize(data, kind):
    image = read_upload(data)
    image.thumbnail((1600, 800 if kind == "header" else 300), Image.Resampling.LANCZOS)
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=True)
    if stream.tell() > 1_400_000:
        raise ValueError("This image is too complex. Upload a smaller image.")
    return base64.b64encode(stream.getvalue()).decode("ascii")


@lru_cache(maxsize=32)
def standard(title, finish):
    profile = default_profile("tickets")
    profile.update(style=finish, title=title)
    return render_menu(profile, title)


def public_files(panel):
    result = []
    try:
        for index, kind in enumerate(("header", "footer")):
            mode = panel.get(kind + "_mode", "ky")
            if mode == "none":
                continue
            if mode == "custom":
                value = panel.get(kind + "_custom")
                if not value:
                    raise ValueError(f"Upload your custom {kind} first.")
                data = base64.b64decode(value, validate=True)
            else:
                title = panel["title"] if kind == "header" else "KY BOT"
                data = standard(title, panel.get(kind + "_finish", "dark_silver"))[index]
            result.append(discord.File(io.BytesIO(data), filename=f"ticket_panel_{kind}.png"))
        return result
    except BaseException:
        for file in result:
            file.close()
        raise
