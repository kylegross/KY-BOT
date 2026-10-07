"""Private generated artwork can be removed by its creator."""

import discord


class ArtworkView(discord.ui.View):
    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=900)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Only the person who created this image can delete it.", ephemeral=True
        )
        return False

    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.defer()
        await interaction.delete_original_response()
        self.stop()
