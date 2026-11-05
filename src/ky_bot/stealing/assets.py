"""Parse Discord assets without accepting arbitrary download hosts."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

EMOJI = re.compile(r"<(?P<animated>a?):(?P<name>[^:\s<>]+):(?P<id>\d{15,22})>")
MESSAGE = re.compile(
    r"https://(?:(?:ptb|canary)\.)?discord(?:app)?\.com/channels/"
    r"(?P<guild>\d{15,22}|@me)/(?P<channel>\d{15,22})/"
    r"(?P<message>\d{15,22})/?$"
)


@dataclass(frozen=True)
class Asset:
    kind: str
    ident: int
    name: str | None
    animated: bool = False
    format: int = 1
    description: str = ""
    tags: str = "🙂"

    @property
    def key(self):
        return f"{self.kind}:{self.ident}"

    @property
    def url(self):
        if self.kind == "emoji":
            return f"https://cdn.discordapp.com/emojis/{self.ident}.webp?animated=true&size=128"
        if self.format == 4:
            return f"https://media.discordapp.net/stickers/{self.ident}.gif"
        extension = "json" if self.format == 3 else "png"
        return f"https://cdn.discordapp.com/stickers/{self.ident}.{extension}"


def emojis(text):
    result = {}
    for match in EMOJI.finditer(text or ""):
        asset = Asset("emoji", int(match["id"]), match["name"], bool(match["animated"]))
        result.setdefault(asset.key, asset)
    return list(result.values())


def sticker(item):
    fmt = getattr(item.format, "value", item.format)
    return Asset(
        "sticker",
        item.id,
        item.name,
        fmt != 1,
        fmt,
        getattr(item, "description", "") or "",
        getattr(item, "emoji", "🙂") or "🙂",
    )


def from_message(message):
    texts = [message.content or ""]
    for embed in message.embeds:
        texts.extend([embed.title or "", embed.description or ""])
        for field in embed.fields:
            texts.extend([field.name, field.value])

    def components(value):
        if isinstance(value, dict):
            if isinstance(value.get("content"), str):
                texts.append(value["content"])
            for child in value.values():
                components(child)
        elif isinstance(value, list):
            for child in value:
                components(child)

    for component in message.components:
        components(component.to_dict())
    result = {a.key: a for a in emojis("\n".join(texts))}
    for item in message.stickers:
        asset = sticker(item)
        result.setdefault(asset.key, asset)
    return list(result.values())


def cdn_id(text, kind):
    text = text.strip()
    if text.isascii() and text.isdigit() and 0 < int(text) < 2**64:
        return int(text)
    url = urlsplit(text)
    if (
        url.scheme != "https"
        or url.hostname not in {"cdn.discordapp.com", "media.discordapp.net", "discord.com"}
        or url.username
        or url.password
        or url.port not in (None, 443)
    ):
        return None
    folder = "emojis" if kind == "emoji" else "stickers"
    match = re.fullmatch(rf"/{folder}/(\d{{15,22}})(?:\.(?:png|webp|gif|json|jpe?g))?", url.path)
    return int(match[1]) if match else None


def name_for(asset, override=None):
    name = override if override is not None else asset.name
    if name is None:
        raise ValueError("The original name is unavailable. Choose a custom name before copying.")
    maximum = 32 if asset.kind == "emoji" else 30
    if not 2 <= len(name) <= maximum or not name.strip():
        raise ValueError(f"The {asset.kind} name must contain 2–{maximum} characters.")
    return name
