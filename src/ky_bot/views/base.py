import logging

import discord

from ky_bot.errors import report_error, send_private

log = logging.getLogger(__name__)


class OwnerView(discord.ui.View):
    """Short-lived controls usable only by the person who opened them."""

    def __init__(self, owner_id: int, *, timeout: float = 180) -> None:
        super().__init__(timeout=timeout)
        self.owner_id = owner_id
        self.message: discord.InteractionMessage | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await send_private(interaction, "Open your own menu with `/help` to use these controls.")
        return False

    async def on_timeout(self) -> None:
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select, discord.ui.ChannelSelect)):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                log.debug("Could not disable an expired menu; its message may have been deleted.")

    async def on_error(
        self, interaction: discord.Interaction, error: Exception, item: discord.ui.Item
    ) -> None:
        await report_error(interaction, error)
