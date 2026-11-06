"""Keep shared settings navigation and explicit private dialogs on separate messages."""

import discord


def is_private_interaction(interaction):
    flags = getattr(getattr(interaction, "message", None), "flags", None)
    return getattr(flags, "ephemeral", False) is True


def private_parent(parent, interaction):
    """Detach a private dialog's Home/Back target from a live shared panel."""
    if parent is None or not getattr(parent, "panel_manager", None):
        return parent
    from ky_bot.admin_functions.settings_view import SettingsView

    detached = SettingsView(interaction.user.id, parent.guild_id, parent.service)
    detached.settings = parent.settings
    detached.guild = interaction.guild
    detached.section = getattr(parent, "section", "overview")
    detached.picker = False
    detached.design = getattr(parent, "design", None)
    return detached


def menu_parent(parent, interaction):
    """Never carry a shared panel reference forward from an ephemeral component."""
    return private_parent(parent, interaction) if is_private_interaction(interaction) else parent


async def defer_menu(interaction, parent=None):
    """Acknowledge a menu update; standalone command menus open privately."""
    if not interaction.response.is_done():
        if parent is None and getattr(interaction, "message", None) is None:
            await interaction.response.defer(ephemeral=True, thinking=True)
        else:
            await interaction.response.defer()


async def display_menu(interaction, view, attachments, parent=None):
    """Edit the shared panel only for shared navigation, otherwise the current response."""
    parent = menu_parent(parent, interaction)
    if parent is not None and getattr(parent, "panel_manager", None):
        return await parent.panel_manager.edit(parent, interaction, view, attachments)
    return await interaction.edit_original_response(
        content=None,
        embeds=[],
        view=view,
        attachments=attachments,
        allowed_mentions=discord.AllowedMentions.none(),
    )
