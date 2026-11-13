"""Public panel banners using the shared KY BOT designs or validated uploads."""

import base64
import io
from functools import lru_cache

import discord
from ky_bot.design.footers import read_upload
from ky_bot.design.menu_design import default_profile, render_menu
from PIL import Image


def normalize(data, kind):
    image = read_upload(data, icon=kind == "footer_icon")
    image.thumbnail(
        (256, 256) if kind == "footer_icon" else (1600, 800 if kind == "header" else 300),
        Image.Resampling.LANCZOS,
    )
    if kind == "footer_icon":
        image.thumbnail((240, 240), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (256, 256))
        canvas.alpha_composite(image, ((256 - image.width) // 2, (256 - image.height) // 2))
        image = canvas
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=True)
    if stream.tell() > 1_400_000:
        raise ValueError("This image is too complex. Upload a smaller image.")
    return base64.b64encode(stream.getvalue()).decode("ascii")


@lru_cache(maxsize=32)
def standard(title, finish, footer_text="", footer_icon=None, footer_background=None):
    profile = default_profile("tickets")
    profile.update(
        style=finish,
        title=title,
        footer_text=footer_text,
        footer_icon=footer_icon,
        footer_background=footer_background,
    )
    return render_menu(profile, title)


def public_files(panel, config=None, token=None, page=0):
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
                if kind == "header" and panel.get("header_title"):
                    from ky_bot.design.headers import render_header

                    data = render_header(
                        data,
                        panel.get("header_finish", "dark_silver"),
                        height=280,
                        title=panel["header_title"],
                    )
            else:
                title = panel.get("header_title")
                if title is None:
                    title = panel["title"]
                title = (title or None) if kind == "header" else "KY BOT"
                data = standard(title, panel.get(kind + "_finish", "dark_silver"))[index]
            if kind == "footer" and panel.get("footer_content", "emblem") in {
                "text",
                "icon",
            }:
                content = panel["footer_content"]
                text = panel.get("footer_banner_text", "") if content == "text" else ""
                icon = panel.get("footer_icon") if content == "icon" else None
                background = panel.get("footer_custom") if mode == "custom" else None
                data = standard(
                    "KY BOT",
                    panel.get("footer_finish", "dark_silver"),
                    text,
                    icon,
                    background,
                )[1]
            result.append(discord.File(io.BytesIO(data), filename=f"ticket_panel_{kind}.png"))
        if config is not None:
            from ky_bot.checklists.artwork import title_image

            from .presentation import heading, heading_file, visible_types

            entries, _, _ = visible_types(config, token, page)
            for key, kind in entries:
                if kind.get("heading_style", "bold") == "decorative":
                    data = title_image(
                        heading(kind),
                        kind.get("heading_finish") or panel.get("header_finish", "dark_silver"),
                        kind.get("heading_color"),
                    )
                    result.append(discord.File(io.BytesIO(data), filename=heading_file(key)))
        from .sections import blocks, image_file

        for position in ("before", "after"):
            for block in blocks(panel, position):
                for part in ("heading", "body"):
                    text = block.get(part, "")
                    if text.strip() and block.get(part + "_style", "plain") == "decorative":
                        from ky_bot.checklists.artwork import title_image

                        data = title_image(
                            text,
                            block.get("finish") or panel.get("header_finish", "dark_silver"),
                            block.get("color"),
                        )
                        result.append(
                            discord.File(
                                io.BytesIO(data),
                                filename=image_file(position, block, part),
                            )
                        )
        return result
    except BaseException:
        for file in result:
            file.close()
        raise
