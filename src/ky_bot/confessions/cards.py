"""One branded, color-matched card for public posts, review and staff logs."""

import base64
import io
from importlib.resources import files

import discord

from ky_bot.views.sections import section_dividers


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


def uploads(config):
    result = []
    for key in ("header", "footer"):
        data = (
            base64.b64decode(config[key])
            if config[key]
            else files("ky_bot.confessions").joinpath("assets", key + ".png").read_bytes()
        )
        result.append(discord.File(io.BytesIO(data), filename="confession_" + key + ".png"))
    return result


class Card(discord.ui.LayoutView):
    def __init__(self, config, title, body, *, detail=None, buttons=(), timeout=None):
        super().__init__(timeout=timeout)
        panel = discord.ui.Container(accent_colour=int(config["color"], 16))
        panel.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://confession_header.png"))
        )
        panel.add_item(discord.ui.TextDisplay("**" + safe(title) + "**\n" + safe(body)))
        if detail:
            panel.add_item(discord.ui.TextDisplay(detail))
        if buttons:
            panel.add_item(discord.ui.ActionRow(*buttons))
        if config["footer_text"]:
            panel.add_item(discord.ui.TextDisplay("-# " + safe(config["footer_text"])))
        panel.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://confession_footer.png"))
        )
        section_dividers(panel)
        self.add_item(panel)


def post_card(config, post, *, staff=False, buttons=()):
    title = config["title"] + " · " + post["id"][:8].upper()
    if post["parent"]:
        title = "ANONYMOUS REPLY · " + post["id"][:8].upper()
    details = []
    if post["parent"]:
        details.append("Reply to confession **" + post["parent"][:8].upper() + "**")
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
    return Card(config, title, post["body"], detail="\n".join(details) or None, buttons=buttons)
