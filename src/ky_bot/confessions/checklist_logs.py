"""Central editing of the checklist's existing log destination."""

import discord

from ky_bot.errors import report_error, send_private

from .command import administrator


class ChecklistLogs(discord.ui.View):
    def __init__(self, service, owner, guild):
        super().__init__(timeout=600)
        self.service, self.owner, self.guild = service, owner, guild
        self.channel = 0
        self.revision = None
        self.boards = discord.ui.ChannelSelect(
            placeholder="Choose a checklist channel", channel_types=[discord.ChannelType.text]
        )
        self.logs = discord.ui.ChannelSelect(
            placeholder="Choose its log channel, or clear to disable",
            channel_types=[discord.ChannelType.text],
            min_values=0,
            max_values=1,
            disabled=True,
        )
        self.boards.callback = self.choose_board
        self.logs.callback = self.choose_logs
        self.add_item(self.boards)
        self.add_item(self.logs)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner or interaction.guild_id != self.guild:
            await send_private(interaction, "Open your own checklist logging controls.")
            return False
        try:
            await administrator(interaction)
            return True
        except ValueError as error:
            await send_private(interaction, str(error))
            return False

    async def choose_board(self, interaction):
        await administrator(interaction)
        board = self.service.store.board(self.boards.values[0].id)
        if not board or board["guild"] != self.guild:
            raise ValueError("Choose an existing checklist channel in this server.")
        self.channel, self.revision = board["channel"], board["revision"]
        self.logs.disabled = False
        self.logs.default_values = (
            [discord.Object(id=board["log_channel"])] if board["log_channel"] else []
        )
        destination = f"<#{board['log_channel']}>" if board["log_channel"] else "Off"
        await interaction.response.edit_message(
            content=f"**CHECKLIST LOGS**\nChecklist: <#{self.channel}> · Logging: {destination}",
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def choose_logs(self, interaction):
        await administrator(interaction)
        channel = self.logs.values[0] if self.logs.values else None
        if channel:
            channel = await self.service.channel(channel.id)
            if channel.guild.id != self.guild or channel.id == self.channel:
                raise ValueError("Choose a separate log channel in this server.")
            self.service.check_permissions(channel, logs=True)
        board = self.service.store.board(self.channel)
        if not board or board["guild"] != self.guild or board["revision"] != self.revision:
            raise ValueError("This checklist changed. Choose it again before saving.")
        self.service.store.update_board(self.channel, log_channel=channel.id if channel else 0)
        self.revision = self.service.store.board(self.channel)["revision"]
        self.service.wakeup.set()
        await interaction.response.edit_message(
            content=f"**CHECKLIST LOGS**\nSaved for <#{self.channel}>: "
            + (f"<#{channel.id}>" if channel else "Off"),
            view=self,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def on_error(self, interaction, error, item):
        if isinstance(error, ValueError):
            await send_private(interaction, str(error))
        else:
            await report_error(interaction, error)
