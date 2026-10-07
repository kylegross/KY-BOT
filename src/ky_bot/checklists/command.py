"""Explicit checklist setup with a Yes/No log choice and private channel picker."""

import asyncio
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from ky_bot.checklists.artwork import board_artwork, png
from ky_bot.checklists.categories import parse_categories
from ky_bot.checklists.service import ChecklistService
from ky_bot.checklists.store import ChecklistStore
from ky_bot.design.footers import MAX_UPLOAD, FooterError, read_upload
from ky_bot.errors import report_error


class LogChannelPicker(discord.ui.View):
    def __init__(self, cog, owner_id, channel_id, guild_id, values, categories):
        super().__init__(timeout=180)
        self.cog, self.owner_id = cog, owner_id
        self.channel_id, self.guild_id = channel_id, guild_id
        self.values, self.categories = values, categories
        self.completed = False
        picker = discord.ui.ChannelSelect(
            placeholder="Choose a checklist log channel",
            channel_types=[discord.ChannelType.text],
            min_values=1,
            max_values=1,
        )

        async def choose(interaction):
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                log_channel = await cog.service.channel(picker.values[0].id)
                if log_channel.guild.id != self.guild_id or log_channel.id == self.channel_id:
                    raise ValueError("Choose a separate checklist log channel in this server.")
                cog.service.check_permissions(log_channel, logs=True)
                summary = await cog.apply_setup(
                    self.channel_id,
                    self.guild_id,
                    {**self.values, "log_channel": log_channel.id},
                    self.categories,
                )
                self.completed = True
                self.stop()
                await interaction.edit_original_response(content=summary)
                await interaction.message.edit(
                    content="Checklist log selected. Setup complete.", view=None
                )
            except (ValueError, FooterError, discord.HTTPException) as exc:
                await interaction.edit_original_response(content=str(exc)[:1500])

        picker.callback = choose
        self.add_item(picker)

    async def interaction_check(self, interaction):
        if (
            not self.completed
            and interaction.guild_id == self.guild_id
            and interaction.user.id == self.owner_id
            and interaction.user.guild_permissions.manage_guild
        ):
            return True
        await interaction.response.send_message(
            "Run /create checklist to start your own setup.", ephemeral=True
        )
        return False

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)


class Checklists(commands.Cog):
    create = app_commands.Group(name="create", description="Create KY BOT tools", guild_only=True)

    def __init__(self, bot):
        self.bot = bot
        path = (
            ":memory:"
            if str(bot.settings.database_path) == ":memory:"
            else Path(bot.settings.database_path).with_name("ky-checklists.sqlite3")
        )
        if bot.settings.database_url:
            from ky_bot.storage.postgres import PostgresChecklistStore

            store = PostgresChecklistStore(bot.settings.database_url)
        else:
            store = ChecklistStore(path)
        self.service = ChecklistService(bot, store)
        bot.checklists = self.service

    async def cog_load(self):
        await self.service.setup()

    async def cog_unload(self):
        self.service.worker.cancel()
        try:
            await self.service.worker
        except asyncio.CancelledError:
            pass
        self.service.store.db.close()

    @commands.Cog.listener()
    async def on_message(self, message):
        await self.service.on_message(message)

    async def apply_setup(self, channel_id, guild_id, values, names):
        service = self.service
        channel = await service.channel(channel_id)
        if channel.guild.id != guild_id:
            raise ValueError("Use a checklist channel in this server.")
        service.check_permissions(channel)
        async with service.lock:
            existing = service.store.board(channel_id)
            candidate = existing or dict(
                name="ADMIN CHECKLIST",
                style="gold",
                height=280,
                footer_text=None,
                header_background=None,
                footer_background=None,
                footer_icon=None,
            )
            candidate = {**candidate, **values}
            await asyncio.to_thread(board_artwork, candidate, [])
            service.store.create(
                channel_id, guild_id, values["log_channel"], name=values.get("name")
            )
            for name in names:
                service.store.add_category(channel_id, name)
            service.store.update_board(channel_id, **values, dirty=1)
            await service.render_board(service.store.board(channel_id))
            service.wakeup.set()
        summary = (
            "Checklist updated. Your tasks are preserved."
            if existing
            else (
                "Checklist created with inline task actions. Authorized messages become tasks; "
                "originals are removed only after saving and preserving attachments."
            )
        )
        destination = values["log_channel"]
        return summary + (
            f"\nChecklist log: <#{destination}>." if destination else "\nChecklist log: Off."
        )

    @create.command(name="checklist", description="Create or update this channel's checklist.")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(
        checklist_log=[
            app_commands.Choice(name="Yes", value="yes"),
            app_commands.Choice(name="No", value="no"),
        ],
        style=[
            app_commands.Choice(name="Gold", value="gold"),
            app_commands.Choice(name="Dark silver", value="dark_silver"),
            app_commands.Choice(name="Neon silver", value="silver_neon"),
        ],
    )
    @app_commands.describe(
        checklist_log="Yes: pick a checklist log channel next. No: create without a log.",
        title="Custom VONCA checklist title; default ADMIN CHECKLIST.",
        categories="Comma-separated categories; adds categories without losing tasks.",
        style="Gold by default. Headings, frame and artwork follow this finish.",
        header_height="Header height, 240–800 pixels; compact 280 by default.",
        header_background="Custom header background; the KY BOT corner logo is retained.",
        footer_background="Optional custom footer background.",
        footer_icon="Custom center icon or emoji image for the footer.",
        footer_text="Optional VONCA footer text; replaces its icon.",
        authorized_role="Optional role allowed to manage tasks alongside administrators.",
    )
    async def checklist(
        self,
        interaction: discord.Interaction,
        checklist_log: str,
        title: app_commands.Range[str, 1, 64] | None = None,
        categories: str | None = None,
        style: str | None = None,
        header_height: app_commands.Range[int, 240, 800] | None = None,
        header_background: discord.Attachment | None = None,
        footer_background: discord.Attachment | None = None,
        footer_icon: discord.Attachment | None = None,
        footer_text: app_commands.Range[str, 1, 64] | None = None,
        authorized_role: discord.Role | None = None,
    ):
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            self.service.check_permissions(interaction.channel)
            names = parse_categories(categories)
            if checklist_log not in {"yes", "no"}:
                raise ValueError("Choose Yes or No for the checklist log.")
            if style is not None and style not in {"gold", "dark_silver", "silver_neon"}:
                raise ValueError("Choose gold, dark silver or neon silver.")
            if authorized_role and authorized_role.guild.id != interaction.guild_id:
                raise ValueError("Choose a role in this server.")
            values = {
                key: value
                for key, value in {
                    "name": title,
                    "style": style,
                    "height": header_height,
                    "footer_text": footer_text,
                    "role_id": authorized_role.id if authorized_role else None,
                }.items()
                if value is not None
            }
            for key, upload in (
                ("header_background", header_background),
                ("footer_background", footer_background),
                ("footer_icon", footer_icon),
            ):
                if upload:
                    if upload.size > MAX_UPLOAD:
                        raise ValueError("Use still PNG, JPEG or WebP images under 8 MB.")
                    data = await upload.read()
                    values[key] = await asyncio.to_thread(
                        lambda: png(read_upload(data, icon=key == "footer_icon"))
                    )
            if footer_icon and footer_text is None:
                values["footer_text"] = None
            if checklist_log == "yes":
                await interaction.followup.send(
                    "Choose a checklist log channel. "
                    "The checklist will be created after you select it.",
                    view=LogChannelPicker(
                        self,
                        interaction.user.id,
                        interaction.channel_id,
                        interaction.guild_id,
                        values,
                        names,
                    ),
                    ephemeral=True,
                )
            else:
                summary = await self.apply_setup(
                    interaction.channel_id,
                    interaction.guild_id,
                    {**values, "log_channel": 0},
                    names,
                )
                await interaction.followup.send(summary, ephemeral=True)
        except (ValueError, FooterError, discord.HTTPException) as exc:
            await interaction.followup.send(str(exc)[:1500], ephemeral=True)


async def setup(bot):
    await bot.add_cog(Checklists(bot))
