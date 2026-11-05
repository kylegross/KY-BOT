"""Validate spoiler markers without downloading or copying members' media."""

import re

MEDIA = re.compile(r"\.(?:png|jpe?g|webp|gif|avif|bmp|heic|mp4|mov|webm|mkv)(?:[?#]|$)", re.I)
LINK = re.compile(r"https?://[^\s<>]+", re.I)


def is_hidden(content, url):
    occurrences = []
    for match in LINK.finditer(content):
        value = match.group().rstrip("|).,!")
        if value == url:
            occurrences.append((match.start(), match.start() + len(value)))
    if not occurrences:
        return False
    spans = [
        (match.start() + 2, match.end() - 2) for match in re.finditer(r"\|\|[\s\S]*?\|\|", content)
    ]
    return all(any(start <= a and b <= end for start, end in spans) for a, b in occurrences)


def violates(message):
    for attachment in message.attachments:
        mime = (attachment.content_type or "").lower()
        if (
            mime.startswith(("image/", "video/")) or MEDIA.search(attachment.filename)
        ) and not attachment.is_spoiler():
            return True
    content = message.content or ""
    urls = {m.group().rstrip("|).,!") for m in LINK.finditer(content)}
    for url in urls:
        if MEDIA.search(url) and not is_hidden(content, url):
            return True
    for embed in message.embeds:
        if (
            embed.type in ("image", "video", "gifv")
            or getattr(embed.image, "url", None)
            or getattr(embed.video, "url", None)
        ):
            url = embed.url
            if url and not is_hidden(content, url):
                return True
    return False
