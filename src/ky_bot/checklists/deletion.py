"""Owner-bound administrator confirmation for removing a checklist."""

import discord

from ky_bot.checklists.inline import InlineView
from ky_bot.errors import send_private


def allowed(interaction, owner, board):
    return bool(
        interaction.guild
        and interaction.guild_id == board["guild"]
        and interaction.channel_id == board["channel"]
        and interaction.user.id == owner
        and interaction.user.guild_permissions.administrator
    )


class DeleteChecklistConfirm(InlineView):
    def __init__(self, service, board, owner):
        super().__init__(timeout=120)
        self.service, self.board, self.owner = service, dict(board), owner
        self.add_item(
            discord.ui.TextDisplay(
                "**DELETE CHECKLIST**\nPermanently remove this board, its tasks, sub-tasks and "
                "categories. New channel messages will no longer become tasks.\n\n"
                "The channel, existing log messages, remaining source messages and discussion "
                "threads will be kept. Deleted source messages cannot be restored. "
                "This cannot be undone."
            )
        )
        remove = discord.ui.Button(label="Delete checklist", style=discord.ButtonStyle.danger)
        remove.callback = self.remove
        self.add_item(remove)
        keep = discord.ui.Button(label="Keep checklist", style=discord.ButtonStyle.secondary)
        keep.callback = self.keep
        self.add_item(keep)

    async def interaction_check(self, interaction):
        if allowed(interaction, self.owner, self.board):
            return True
        await send_private(
            interaction,
            "Only the administrator who opened this confirmation "
            "can use it, in the checklist's own channel.",
        )
        return False

    async def remove(self, interaction):
        # Recheck here as well: permission changes must not leave an old confirmation usable.
        if not await self.interaction_check(interaction):
            return
        await interaction.response.defer()
        try:
            async with self.service.lock:
                self.service.store.delete_board(
                    self.board["channel"],
                    self.board["guild"],
                    self.board["revision"],
                    self.board["message"],
                )
                warning = ""
                if self.board["message"]:
                    try:
                        channel = await self.service.channel(self.board["channel"])
                        await channel.get_partial_message(self.board["message"]).delete()
                    except discord.NotFound:
                        pass
                    except discord.HTTPException:
                        warning = (
                            "\nThe checklist is disabled, but I could not remove its "
                            "old panel. You can delete that message manually."
                        )
                self.service.wakeup.set()
            text = (
                "**CHECKLIST DELETED**\nThis channel no longer converts messages into tasks."
                + warning
            )
        except ValueError as error:
            await send_private(interaction, str(error))
            return
        await interaction.edit_original_response(
            content=None, view=InlineView().add_item(discord.ui.TextDisplay(text))
        )
        self.stop()

    async def keep(self, interaction):
        if not await self.interaction_check(interaction):
            return
        await interaction.response.edit_message(
            content=None,
            view=InlineView().add_item(
                discord.ui.TextDisplay("**CHECKLIST KEPT**\nYour board and tasks are unchanged.")
            ),
        )
        self.stop()


async def open_delete(service, interaction, channel):
    board = service.store.board(channel)
    if not board or not allowed(interaction, interaction.user.id, board):
        await send_private(
            interaction,
            "Only a server administrator can delete this checklist from its own channel.",
        )
        return
    await interaction.response.send_message(
        view=DeleteChecklistConfirm(service, board, interaction.user.id), ephemeral=True
    )
