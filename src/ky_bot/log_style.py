"""One decorated title and emblem footer for all staff logs."""

import io
from importlib.resources import files

import discord

PREFIX = "\\*ੈ𑁍  "
SUFFIX = "  𑁍ੈ\\*"
ICON = "attachment://ky_bot_logo.png"


def decorated_title(title):
    """Use the exact same title ornaments for logs and ticket confirmations."""
    if title.startswith(PREFIX) and title.endswith(SUFFIX):
        return title
    return PREFIX + title[: 256 - len(PREFIX) - len(SUFFIX)] + SUFFIX


def style_log(embed):
    embed.title = decorated_title(embed.title or "ACTIVITY LOG")
    if embed.timestamp is None:
        embed.timestamp = discord.utils.utcnow()
    embed.set_footer(text="KY BOT", icon_url=ICON)
    return embed


async def send_log(target, embed, *, edit=False):
    """Use only the shipped branding asset; never attach member media to logs."""
    style_log(embed)
    logo = discord.File(
        io.BytesIO(
            files("ky_bot.design")
            .joinpath("assets", "footer", "ky_member_log_logo.png")
            .read_bytes()
        ),
        filename="ky_bot_logo.png",
    )
    try:
        if edit:
            return await target.edit(
                embed=embed, attachments=[logo], allowed_mentions=discord.AllowedMentions.none()
            )
        return await target.send(
            embed=embed, file=logo, allowed_mentions=discord.AllowedMentions.none()
        )
    finally:
        logo.close()
