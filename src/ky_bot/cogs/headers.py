"""Private slogan-free header exports with user-provided backgrounds."""

import asyncio
import io
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ky_bot.services.footers import MAX_UPLOAD, FooterError
from ky_bot.services.headers import render_header

if TYPE_CHECKING:
    from ky_bot.bot import KYBot


class Headers(commands.Cog):
    def __init__(self, bot: "KYBot") -> None:
        self.bot = bot
        self.render_lock = asyncio.Lock()

    @app_commands.command(name="header", description="Create a KY BOT header with your own image.")
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 15, key=lambda interaction: interaction.user.id)
    @app_commands.choices(
        style=[
            app_commands.Choice(name="Metallic gold", value="gold"),
            app_commands.Choice(name="Iridescent silver · rainbow tint", value="silver_neon"),
            app_commands.Choice(name="Dark metallic silver", value="dark_silver"),
        ]
    )
    @app_commands.describe(
        background="Your background: a still PNG, JPEG or WebP, up to 8 MB.",
        style="Choose the metallic finish for the KY BOT mark and frame.",
        framed="Include a thin outline matching the selected finish.",
        title="Optional menu heading in VONCA. Leave empty for an open header.",
        crop_x="Background focal point: 0 left, 50 center, 100 right.",
        crop_y="Background focal point: 0 top, 50 center, 100 bottom.",
        dim="Darken your background by this percentage.",
        height="Header height in pixels: 240–800. Default 520; try 280 for a compact banner.",
    )
    async def header(
        self,
        interaction: discord.Interaction,
        background: discord.Attachment,
        style: app_commands.Choice[str],
        framed: bool = False,
        title: app_commands.Range[str, 1, 64] | None = None,
        crop_x: app_commands.Range[int, 0, 100] = 50,
        crop_y: app_commands.Range[int, 0, 100] = 50,
        dim: app_commands.Range[int, 0, 80] = 0,
        height: app_commands.Range[int, 240, 800] = 520,
    ) -> None:
        if background.size > MAX_UPLOAD:
            await interaction.response.send_message("Use an image under 8 MB.", ephemeral=True)
            return
        if self.render_lock.locked():
            await interaction.response.send_message(
                "I'm finishing another header. Please try again in a moment.",
                ephemeral=True,
            )
            return
        async with self.render_lock:
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                result = await asyncio.to_thread(
                    render_header,
                    await background.read(),
                    style.value,
                    framed=framed,
                    title=title,
                    crop_x=crop_x,
                    crop_y=crop_y,
                    dim=dim,
                    height=height,
                )
            except FooterError as exc:
                await interaction.followup.send(str(exc), ephemeral=True)
                return
            except discord.HTTPException:
                await interaction.followup.send(
                    "I couldn't read that attachment. Please upload it again.",
                    ephemeral=True,
                )
                return
        await interaction.followup.send(
            "Your header is ready. Run `/header` again to adjust the height, crop or finish.",
            file=discord.File(io.BytesIO(result), filename=f"ky_header_{style.value}.png"),
            ephemeral=True,
        )


async def setup(bot: "KYBot") -> None:
    await bot.add_cog(Headers(bot))
