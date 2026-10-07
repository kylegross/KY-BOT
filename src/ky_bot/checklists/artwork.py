"""Use the command center's exact heading typography for checklist sections."""

import io

from PIL import Image, ImageOps

from ky_bot.design.collection import COLOURS, floral_background
from ky_bot.design.footers import centered_overlay, read_upload, rectangular_frame
from ky_bot.design.headers import render_header
from ky_bot.design.typography import text_mask


def png(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


def title_image(title, style):
    from ky_bot.design.typography import TypographyError

    text = title.upper()
    while True:
        try:
            mask = text_mask(text, role="display", size=36, min_size=36, max_width=1080)
            break
        except TypographyError:
            if len(text) <= 4:
                raise
            text = text.rstrip("…")[:-1] + "…"
    ink = Image.new("RGBA", mask.size, COLOURS[style])
    ink.putalpha(mask)
    label = Image.new("RGBA", (1120, mask.height + 32))
    label.alpha_composite(ink, (2, 16))
    return png(label)


def board_artwork(board, categories, tasks=None):
    style, height = board["style"], board["height"]
    background = board["header_background"] or png(floral_background(style, (1600, height)))
    result = {
        "checklist_header.png": render_header(
            background, style, framed=True, height=height, title=board["name"]
        )
    }
    size = (1600, 150)
    footer = (
        ImageOps.fit(read_upload(board["footer_background"]), size, Image.Resampling.LANCZOS)
        if board["footer_background"]
        else floral_background(style, size)
    )
    if board["footer_text"]:
        mask = text_mask(board["footer_text"], role="display", size=52, min_size=24, max_width=1000)
        ink = Image.new("RGBA", mask.size, COLOURS[style])
        ink.putalpha(mask)
        footer.alpha_composite(ink, ((size[0] - mask.width) // 2, (size[1] - mask.height) // 2))
    elif board["footer_icon"]:
        icon = ImageOps.contain(
            read_upload(board["footer_icon"], icon=True), (85, 85), Image.Resampling.LANCZOS
        )
        footer.alpha_composite(icon, ((size[0] - icon.width) // 2, (size[1] - icon.height) // 2))
    else:
        footer.alpha_composite(centered_overlay(style, size))
    footer.alpha_composite(rectangular_frame(style, size, radius=8, inset=10))
    result["checklist_footer.png"] = png(footer)
    visible_names = None
    if tasks is not None:
        from ky_bot.checklists.board import page_entries

        entries, _, _ = page_entries(board, tasks, categories)
        visible_names = {name or "Uncategorized" for name, _, _ in entries}
    if visible_names is None or "Uncategorized" in visible_names:
        result["category_none.png"] = title_image("UNCATEGORIZED", style)
    for category in categories:
        if visible_names is not None and category["name"] not in visible_names:
            continue
        result[f"category_{category['id']}.png"] = title_image(category["name"], style)
    return result
