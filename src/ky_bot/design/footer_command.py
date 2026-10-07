"""Private footer exports with user-supplied background and branding."""

import asyncio
import io
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ky_bot.design.artwork_view import ArtworkView
from ky_bot.design.footers import MAX_UPLOAD, SLOGAN, FooterError, render_footer

if TYPE_CHECKING:
    from ky_bot.bot import KYBot


class Footers(commands.Cog):
    def __init__(self, bot: "KYBot") -> None:
        self.bot = bot
        self.render_lock = asyncio.Lock()

    @app_commands.command(
        name="footer", description="Create a footer with your background and branding."
    )
    @app_commands.guild_only()
    @app_commands.checks.cooldown(1, 15, key=lambda interaction: interaction.user.id)
    @app_commands.choices(
        style=[
            app_commands.Choice(name="Iridescent silver · rainbow tint", value="silver_neon"),
            app_commands.Choice(name="Metallic gold", value="gold"),
            app_commands.Choice(name="Dark metallic silver", value="dark_silver"),
        ]
    )
    @app_commands.describe(
        background="Upload your background: PNG, JPEG or WebP, up to 8 MB.",
        style="Choose the frame, text and icon finish.",
        slogan="Optional custom slogan (requires the owner's installed Vonca font).",
        left_icon="Transparent logo, tinted to match the frame; used on both sides by default.",
        right_icon="Optional separate transparent logo for the right side.",
        crop_x="Background focal point: 0 is left, 50 center, 100 right.",
        crop_y="Background focal point: 0 is top, 50 center, 100 bottom.",
        dim="Darken the background by this percentage to help the lettering stand out.",
        icon_scale="Icon size percentage: 60–120.",
    )
    async def footer(
        self,
        interaction: discord.Interaction,
        background: discord.Attachment,
        style: app_commands.Choice[str],
        slogan: app_commands.Range[str, 1, 80] | None = None,
        left_icon: discord.Attachment | None = None,
        right_icon: discord.Attachment | None = None,
        crop_x: app_commands.Range[int, 0, 100] = 50,
        crop_y: app_commands.Range[int, 0, 100] = 50,
        dim: app_commands.Range[int, 0, 80] = 20,
        icon_scale: app_commands.Range[int, 60, 120] = 100,
    ) -> None:
        attachments = [a for a in (background, left_icon, right_icon) if a is not None]
        if any(a.size > MAX_UPLOAD for a in attachments):
            await interaction.response.send_message(
                "Each image must be under 8 MB.", ephemeral=True
            )
            return
        if self.render_lock.locked():
            await interaction.response.send_message(
                "I'm finishing another footer. Please try again in a moment.", ephemeral=True
            )
            return
        async with self.render_lock:
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                background_data = await background.read()
                left_data = await left_icon.read() if left_icon else None
                right_data = await right_icon.read() if right_icon else left_data
                result = await asyncio.to_thread(
                    render_footer,
                    background_data,
                    style.value,
                    centered=slogan is None and left_icon is None and right_icon is None,
                    slogan=slogan if slogan is not None else SLOGAN,
                    left_icon=left_data,
                    right_icon=right_data,
                    crop_x=crop_x,
                    crop_y=crop_y,
                    dim=dim,
                    icon_scale=icon_scale,
                    font_path=self.bot.settings.footer_font_path,
                )
            except FooterError as error:
                await interaction.followup.send(str(error), ephemeral=True)
                return
            except discord.HTTPException:
                await interaction.followup.send(
                    "I couldn't read that attachment. Please upload it again.", ephemeral=True
                )
                return
        await interaction.followup.send(
            "Your footer is ready. Run `/footer` again to adjust the crop, slogan or icons.",
            file=discord.File(io.BytesIO(result), filename=f"ky_footer_{style.value}.png"),
            view=ArtworkView(interaction.user.id),
            ephemeral=True,
        )


async def setup(bot: "KYBot") -> None:
    await bot.add_cog(Footers(bot))
