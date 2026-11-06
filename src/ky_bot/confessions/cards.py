"""One branded, color-matched card for public posts, review and staff logs."""

import base64
import io
from datetime import UTC, datetime

import discord

from ky_bot.log_style import style_log
from ky_bot.views.media import NoAltMediaGalleryItem
from ky_bot.views.sections import section_dividers

from .appearance import standard_artwork
from .headings import DIVIDER, title_image


def safe(text):
    return discord.utils.escape_mentions(str(text))


def privacy(config):
    parts = ["Your name and avatar will not appear in the public channel."]
    if config["approval"]:
        parts.append(
            "Administrator approval is on. Administrators can see your identity in "
            "the private review queue."
        )
    if config["logging"]:
        parts.append(
            "Author logging is on. Your identity and submission will be saved in "
            "the private staff log."
        )
    elif not config["approval"]:
        parts.append("Author logging is off.")
    else:
        parts.append(
            "Ongoing author logging is off; the private approval queue still identifies you."
        )
    return "\n".join(parts)


def uploads(config, view=None):
    result = []
    for key in ("header", "footer"):
        data = (
            view.header_art
            if key == "header" and view is not None and getattr(view, "header_art", None)
            else base64.b64decode(config[key])
            if config[key]
            else standard_artwork(
                key,
                config.get("artwork_finish", "silver_neon"),
                config.get("header_title", "CONFESSIONS"),
                config.get("text_finish", "silver_neon"),
                config.get("text_color"),
            )
        )
        result.append(discord.File(io.BytesIO(data), filename="confession_" + key + ".png"))
    if view is not None and getattr(view, "title_art", None):
        result.append(discord.File(io.BytesIO(view.title_art), filename="confession_title.png"))
    return result


class Card(discord.ui.LayoutView):
    def __init__(
        self,
        config,
        title,
        body,
        *,
        detail=None,
        buttons=(),
        timeout=None,
        show_footer_text=True,
        large_body=False,
    ):
        super().__init__(timeout=timeout)
        panel = discord.ui.Container(accent_colour=int(config["color"], 16))
        panel.add_item(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://confession_header.png"))
        )
        self.title_art = title_image(
            title, config.get("text_finish", "silver_neon"), config.get("text_color")
        )
        panel.add_item(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://confession_title.png"))
        )
        content = safe(body)
        if large_body:
            # Native Discord headings are larger and bold, including wrapped lines.
            content = "\n".join(
                "## " + line if line.strip() else "" for line in content.splitlines()
            )
            if len(content) > 3000:
                # Keep all words visible for unusually line-heavy submissions.
                content = "## " + " ".join(safe(body).splitlines())
        panel.add_item(discord.ui.TextDisplay(content))
        if detail:
            panel.add_item(discord.ui.TextDisplay(detail))
        if buttons:
            panel.add_item(discord.ui.ActionRow(*buttons))
        if show_footer_text and config["footer_text"]:
            panel.add_item(discord.ui.TextDisplay("-# " + safe(config["footer_text"])))
        panel.add_item(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://confession_footer.png"))
        )
        section_dividers(panel)
        self.add_item(panel)


def post_card(config, post, *, staff=False, buttons=()):
    title = config["title"] + DIVIDER + str(post["data"]["number"])
    if post["parent"]:
        title = "ANONYMOUS REPLY" + DIVIDER + str(post["data"]["number"])
    details = []
    if post["parent"]:
        details.append("Reply to confession **" + str(post["data"]["parent_number"]) + "**")
    if staff:
        author = (
            f"<@{post['author']}> · User ID: `{post['author']}`"
            if post["author"]
            else "Identity logging was off."
        )
        details.extend(
            (
                f"**Author:** {author}",
                f"**Destination:** <#{post['channel']}>",
                f"**Status:** {post['state']}",
            )
        )
    if post["data"].get("message"):
        url = (
            f"https://discord.com/channels/{post['guild']}/{post['channel']}/"
            f"{post['data']['message']}"
        )
        details.append(f"[View confession]({url})")
    return Card(
        config,
        title,
        post["body"],
        detail="\n".join(details) or None,
        buttons=buttons,
        show_footer_text=staff and config["footer_text"] != "KY BOT · Confessions",
        large_body=not staff,
    )


def author_log_embed(config, post, display_name=None):
    """Match Server Activity's decorated event title, stacked fields and footer."""
    event = "CONFESSION REPLY POSTED" if post["parent"] else "CONFESSION POSTED"
    author = (
        safe(discord.utils.escape_markdown(display_name))
        if display_name
        else f"<@{post['author']}>"
        if post["author"]
        else "Not logged"
    )
    if post["author"]:
        author += f" · User ID: `{post['author']}`"
    embed = discord.Embed(
        title=f"\\*ੈ𑁍  {event}  𑁍ੈ\\*",
        colour=int(config["color"], 16),
        timestamp=datetime.fromtimestamp(post["created"], UTC),
    )
    embed.add_field(name="Author", value=author, inline=False)
    embed.add_field(name="Channel", value=f"<#{post['channel']}>", inline=False)
    identifier = str(post["data"]["number"])
    if post["data"].get("message"):
        url = (
            f"https://discord.com/channels/{post['guild']}/{post['channel']}/"
            f"{post['data']['message']}"
        )
        identifier = f"[{identifier}]({url})"
    embed.add_field(name="Confession ID", value=identifier, inline=False)
    if post["parent"]:
        embed.add_field(name="Reply to", value=str(post["data"]["parent_number"]), inline=False)
    body = safe(post["body"])
    for offset in range(0, len(body), 1024):
        embed.add_field(
            name="Confession text" if offset == 0 else "Continued",
            value=body[offset : offset + 1024],
            inline=False,
        )
    return style_log(embed)
