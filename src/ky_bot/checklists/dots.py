"Small pastel priority emoji, with a built-in fallback when slots are unavailable."

import logging
import time
from importlib.resources import files

import discord


class PriorityDots:
    def __init__(self):
        self.cache = {}

    async def for_guild(self, guild):
        cached = self.cache.get(guild.id)
        if cached and time.monotonic() < cached[0]:
            return cached[1]
        dots = {"high": "🔴", "medium": "🟡", "low": "🟢"}
        try:
            emojis = {e.name: e for e in await guild.fetch_emojis()}
            for priority in dots:
                name = "ky_priority_" + priority
                emoji = emojis.get(name)
                if emoji is None:
                    image = (
                        files("ky_bot.checklists")
                        .joinpath("assets", f"priority_{priority}.png")
                        .read_bytes()
                    )
                    emoji = await guild.create_custom_emoji(
                        name=name, image=image, reason="Pastel checklist priority indicators"
                    )
                dots[priority] = str(emoji)
        except (discord.HTTPException, OSError):
            logging.warning("Checklist priority emoji unavailable; using standard circles")
        self.cache[guild.id] = (time.monotonic() + 600, dots)
        return dots
