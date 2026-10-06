import discord

from ky_bot.views.base import OwnerView

PAGES = {
    "home": (
        "Welcome to KY BOT",
        "Your server toolkit, one interaction away.\n\n"
        "**Available now**\n`/help` — explore this menu\n`/ping` — check bot responsiveness\n"
        "`/settings` — server configuration (admins)\n"
        "`/footer` — custom background and branding footer\n\n"
        "`/header` — branded header with your own background\n\n"
        "Choose a category below to see what’s planned.",
    ),
    "moderation": (
        "Moderation",
        "Planned: moderation actions, permission checks, and case history.\n\n"
        "No moderation commands are enabled yet.",
    ),
    "automation": (
        "Automation",
        "Planned: configurable autodelete and automatic message threads.\n\n"
        "No messages are deleted or threaded by this foundation.",
    ),
    "utilities": (
        "Utilities & games",
        "Available: `/ping` to check responsiveness, `/footer` for branded footers, "
        "and `/header` for menu and embed header graphics.\n\n"
        "Planned: server utilities, interactive games, and reusable menus.",
    ),
    "admin": (
        "Admin tools",
        "Available: `/settings` — persistent server settings (administrators only).\n\n"
        "Choose or clear a log channel for future moderation. Logging is not active yet.",
    ),
}


def help_embed(page: str = "home") -> discord.Embed:
    title, description = PAGES[page]
    embed = discord.Embed(title=title, description=description, colour=0x8B5CF6)
    embed.set_author(name="KY BOT • Command Center")
    embed.set_footer(text="Only you can see this menu • Expires after 3 minutes of inactivity")
    return embed


class HelpView(OwnerView):
    @discord.ui.select(
        placeholder="Explore KY BOT…",
        options=[
            discord.SelectOption(label="Overview", value="home", emoji="🏠"),
            discord.SelectOption(label="Moderation", value="moderation", emoji="🛡️"),
            discord.SelectOption(label="Automation", value="automation", emoji="⚙️"),
            discord.SelectOption(label="Utilities & games", value="utilities", emoji="🎮"),
            discord.SelectOption(label="Admin tools", value="admin", emoji="🔧"),
        ],
    )
    async def category(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        page = select.values[0]
        for option in select.options:
            option.default = option.value == page
        await interaction.response.edit_message(embed=help_embed(page), view=self)

    @discord.ui.button(label="Close menu", style=discord.ButtonStyle.secondary)
    async def close_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            content="Menu closed. Use `/help` to reopen.", embed=None, view=None
        )
        self.stop()
