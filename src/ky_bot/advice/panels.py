"""Compact branded Components V2 panels with right-hand role controls."""

import io

import discord
from PIL import Image, ImageDraw, ImageOps

from ky_bot.confessions.appearance import configured_artwork, standard_artwork
from ky_bot.confessions.headings import lettering, title_image
from ky_bot.design.collection import COLOURS
from ky_bot.design.image_text import metallic_ink
from ky_bot.views.media import NoAltMediaGalleryItem


def safe(value):
    return discord.utils.escape_mentions(str(value))


def artwork_bytes(config, kind):
    return configured_artwork(config, kind, renderer=standard_artwork)


def heading_image(text, config, decorated=False):
    if not decorated:
        return title_image(
            text,
            config["text_finish"],
            config["text_color"],
            shimmer=True,
            line_color=config.get("line_color"),
        )
    mask = ImageOps.contain(lettering(text), (760, 32), Image.Resampling.LANCZOS)
    image = Image.new("RGBA", (1120, mask.height + 32))
    left = (1120 - mask.width) // 2
    image.alpha_composite(
        metallic_ink(mask, config["text_finish"], config["text_color"]), (left, 16)
    )
    color = config.get("line_color") or config["text_color"] or COLOURS[config["text_finish"]]
    pen = ImageDraw.Draw(image)
    middle = image.height // 2
    pen.line((2, middle, left - 18, middle), fill=color, width=2)
    pen.line((left + mask.width + 18, middle, 1118, middle), fill=color, width=2)
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


class Panel(discord.ui.LayoutView):
    def __init__(self, config, *, banner=True):
        super().__init__(timeout=None)
        self.files = {}
        self.panel = discord.ui.Container(accent_colour=int(config["color"], 16))
        self.add_item(self.panel)
        if banner:
            for key in ("header",):
                data = artwork_bytes(config, key)
                self.image("advice_" + key + ".png", data)

    def image(self, name, data):
        self.files[name] = data
        self.panel.add_item(discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://" + name)))

    def heading(self, text, config, decorated=False):
        if text.strip():
            self.image(
                f"advice_heading_{len(self.files)}.png", heading_image(text, config, decorated)
            )

    def footer(self, config, *, divider=True):
        if config["footer_text"] and config["footer_text"] != "KY BOT · Ask for Advice":
            self.panel.add_item(discord.ui.TextDisplay("-# " + safe(config["footer_text"])))
        data = artwork_bytes(config, "footer")
        if divider:
            self.panel.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
        self.image("advice_footer.png", data)

    def uploads(self):
        return [discord.File(io.BytesIO(raw), filename=name) for name, raw in self.files.items()]


def access_panel(channel, config, *, preview=False):
    from .command import Action

    view = Panel(config)
    for index, section in enumerate(config["sections"]):
        if index:
            view.panel.add_item(
                discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.small)
            )
        style = config | config.get("section_styles", {}).get(str(index), {})
        view.heading(section["heading"], style, section["decorated"])
        for row_index, row in enumerate(section["rows"]):
            text = safe(row["text"])
            if row_index < len(section["rows"]) - 1:
                # Use the same compact blank line as Command Center control rows.
                text += "\n-# \u200b"
            heading = row.get("heading", "").strip()
            if heading:
                label = discord.utils.escape_markdown(safe(heading))
                text = f"**{label}**\n{text}"
            if row["action"] == "none":
                view.panel.add_item(discord.ui.TextDisplay(text))
            else:
                button = Action(channel, row["action"], label=row["label"], emoji=row["emoji"])
                button.item.disabled = preview
                view.panel.add_item(discord.ui.Section(text, accessory=button))
    view.footer(config, divider=False)
    return view


def submission_panel(channel, config, *, preview=False):
    from .command import Action

    view = Panel(config)
    view.heading(config["panel_title"], config, True)
    button = Action(channel, "ask", label=config["submit_label"])
    button.item.disabled = preview
    text = safe(config["panel_text"])
    instruction = config.get("panel_command_text", "Use the button below or `/advice` command.")
    view.panel.add_item(discord.ui.TextDisplay(text))
    if instruction.strip():
        view.panel.add_item(discord.ui.TextDisplay(safe(instruction)))
    view.panel.add_item(discord.ui.ActionRow(button))
    view.footer(config, divider=False)
    return view


def post_panel(post, config, *, review=False):
    from .command import Action

    view = Panel(config)
    number = post["data"]["number"]
    title = (
        f"{config['response_title']} · QUESTION #{number}"
        if post["parent"]
        else f"{config['title']} #{number}"
    )
    view.heading(title, config)
    view.panel.add_item(discord.ui.TextDisplay(safe(post["body"])))
    if post["parent"]:
        name = post["data"].get("public_author")
        view.panel.add_item(
            discord.ui.TextDisplay(f"-# Response from <@{name}>" if name else "-# Anonymous advice")
        )
        if post["data"].get("is_original_poster"):
            view.panel.add_item(discord.ui.TextDisplay("**Original Poster**"))
    if review:
        author = post["author"]
        view.panel.add_item(
            discord.ui.TextDisplay(
                f"**Private review** · Author: <@{author}> (`{author}`)\n"
                f"Public identity: {'shown' if post['data'].get('public_author') else 'anonymous'}"
            )
        )
        view.panel.add_item(
            discord.ui.ActionRow(Action(post["id"], "approve"), Action(post["id"], "reject"))
        )
    else:
        view.panel.add_item(
            discord.ui.ActionRow(
                Action(
                    post["id"],
                    "respond",
                    label="Reply" if post["parent"] else "Respond to question",
                )
            )
        )
    # Stable reference also lets delivery recovery find a post after a connection failure.
    view.panel.add_item(discord.ui.TextDisplay(f"-# Reference: {post['id']}"))
    view.footer(config)
    return view
