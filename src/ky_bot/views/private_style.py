"""Shared titles for private menus; stars are literal, never italic delimiters."""

import discord


def private_title(title: str, *, escaped: bool = False) -> str:
    title = " ".join(title.split())
    if not escaped:
        title = discord.utils.escape_markdown(discord.utils.escape_mentions(title))
    return f"\\*ੈ𑁍  {title}  𑁍ੈ\\*"


def private_heading(title: str, *, escaped: bool = False) -> str:
    return "## " + private_title(title, escaped=escaped)


def style_private_heading(content: str) -> str:
    """Upgrade existing heading declarations without styling body/status messages."""
    first, separator, rest = content.partition("\n")
    if first.startswith("## "):
        title = first[3:]
        if "ੈ𑁍  " in title and "  𑁍ੈ" in title:
            return content
    elif first.startswith("**") and first.endswith("**"):
        title = first[2:-2]
    else:
        return content
    return private_heading(title, escaped=True) + separator + rest
