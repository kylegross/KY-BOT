"""Generated graphics explicitly clear optional Discord image descriptions."""

import discord


class NoAltMediaGalleryItem(discord.MediaGalleryItem):
    def to_dict(self):
        payload = super().to_dict()
        # discord.py omits None. Send null explicitly when replacing old components.
        payload["description"] = None
        return payload
