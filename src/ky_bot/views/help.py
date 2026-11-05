"""Private help center using KY BOT's dark silver artwork and inline navigation."""

import io
import logging
from importlib.resources import files

import discord

from ky_bot.errors import report_error, send_private
from ky_bot.views.sections import section_dividers

log = logging.getLogger(__name__)

GUIDES = {
    "requests": (
        "ASK TO DM/FR",
        (
            (
                "ASK FIRST",
                "Open your server’s **Ask to DM/FR** channel and select "
                "**Ask to DM/FR**. Choose a member, request type and optional private reason, "
                "then confirm. Wait for acceptance before contacting them.",
            ),
            (
                "YOUR REQUESTS",
                "Select **My requests** on the channel panel to respond, "
                "cancel an unanswered request or withdraw permission you previously gave. "
                "You can also use the buttons in KY BOT’s private notifications.",
            ),
            (
                "PRIVACY & CONSENT",
                "Recipients can accept or decline. Unanswered requests "
                "expire after **48 hours**. Reasons stay private; staff can review request logs. "
                "When DMs are blocked, delivery uses a private thread accessible to that member "
                "and staff with Manage Threads.",
            ),
            (
                "SERVER RULES",
                "Your server controls eligible roles and whether request cards "
                "appear publicly. The channel’s entry menu stays visible even when requests "
                "are hidden. Ask a server administrator if the feature is unavailable.",
            ),
        ),
    ),
    "checklists": (
        "CHECKLISTS",
        (
            (
                "ADD A TASK",
                "In a configured checklist channel, an authorized member’s "
                "message becomes a task. KY BOT saves the task and its attachments before "
                "removing the original message.",
            ),
            (
                "MANAGE YOUR BOARD",
                "Use the inline **Manage** button beside a task to access "
                "its available actions. Depending on your permissions, you can edit or "
                "reorder tasks, mark them complete and open a private discussion thread.",
            ),
            (
                "DELETE A CHECKLIST",
                "Server administrators can select **Delete checklist** at the bottom "
                "of the board, then confirm in the private prompt. This removes the board "
                "and its tasks, sub-tasks and categories. The channel, posted logs and "
                "discussion threads are kept.",
            ),
            (
                "WHO CAN USE IT",
                "Task actions follow the board’s administrator and authorized-role "
                "rules. Ask a server administrator for access or to configure a checklist.",
            ),
        ),
    ),
    "leveling": (
        "LEVELING & REWARDS",
        (
            (
                "EARN XP",
                "When enabled by your server, eligible messages earn XP. Voice XP "
                "is optional. Cooldowns, excluded channels and excluded roles determine "
                "which activity counts.",
            ),
            (
                "LEVEL UP",
                "Your server can configure level rewards and member rankings. "
                "Available rewards depend on the roles and settings chosen by administrators.",
            ),
            (
                "NEED AN ADJUSTMENT?",
                "Ask an administrator to review your XP. Member "
                "corrections are restricted to server administrators.",
            ),
        ),
    ),
    "essentials": (
        "QUICK REFERENCE",
        (
            ("OPEN HELP", "`/help` opens this private guide. Only you can use its controls."),
            (
                "CHECK KY BOT",
                "`/ping` checks whether KY BOT is responding and shows its "
                "gateway heartbeat latency.",
            ),
            (
                "CHANNEL MENUS",
                "Use the server’s published panels for consent requests and "
                "checklists. Features depend on your server’s configuration and your roles.",
            ),
            (
                "CONTROLS EXPIRED?",
                "Run `/help` again for a fresh menu. For other expired forms, "
                "open a new form from the feature’s channel panel.",
            ),
        ),
    ),
    "admin": (
        "COMMAND CENTER",
        (
            (
                "SERVER CONFIGURATION",
                "Run `/settings` as a server administrator. Choose "
                "a **Command Center channel** to keep a permanent management panel in your server.",
            ),
            (
                "ASK TO DM/FR",
                "Choose request and staff log channels, set sender and recipient "
                "role permissions, and choose whether requests appear in the channel. Select "
                "**Publish / refresh**, then **Enable**.",
            ),
            (
                "MEMBER EXPERIENCE",
                "Configure welcomes, Leveling, server activity logs and "
                "embed design from their Command Center sections.",
            ),
            (
                "XP CORRECTIONS",
                "`/xp add` · `/xp remove` · `/xp level`\n"
                "Administrator-only commands for adjusting a member’s XP or level.",
            ),
        ),
    ),
    "automation": (
        "AUTOMATION & SETUP",
        (
            (
                "GENERAL SETTINGS",
                "Open **Command Center → General Settings** to manage "
                "Autodelete, Autothread and Autoreact. Server administrators manage these "
                "through the Command Center.",
            ),
            (
                "AUTOMATION COMMANDS",
                "`/autodelete` · `/autothread` · `/autoreact`\n"
                "These configuration commands require **Manage Server**. Review the selected "
                "channels, permissions and rules before enabling automation.",
            ),
            (
                "CREATE A CHECKLIST",
                "`/create checklist`\nRequires **Manage Server**. "
                "Choose Yes for a separate checklist log channel or No to create without one. "
                "An authorized role and custom title are optional.",
            ),
        ),
    ),
}


def help_files():
    return [
        discord.File(
            io.BytesIO(files("ky_bot.design").joinpath("assets", directory, name).read_bytes()),
            filename=name,
        )
        for directory, name in (("header", "ky_header_help.png"), ("footer", "ky_footer_help.png"))
    ]


class HelpView(discord.ui.LayoutView):
    def __init__(self, owner_id, guild_id, *, administrator=False, manage_guild=False):
        super().__init__(timeout=600)
        self.owner_id, self.guild_id = owner_id, guild_id
        self.administrator = administrator
        self.manage_guild = manage_guild or administrator
        self.message = None
        self.page = "home"
        self.show("home")

    def button(self, label, callback):
        item = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)
        item.callback = callback
        return item

    def navigation(self, label, page):
        async def navigate(interaction):
            await interaction.response.defer()
            self.show(page)
            self.message = await interaction.edit_original_response(view=self)

        return self.button(label, navigate)

    def show(self, page):
        if page == "admin" and not self.administrator:
            page = "home"
        if page == "automation" and not self.manage_guild:
            page = "home"
        self.page = page
        self.clear_items()
        panel = discord.ui.Container(accent_colour=0xBDC3CA)
        panel.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://ky_header_help.png"))
        )
        if page == "home":
            panel.add_item(
                discord.ui.TextDisplay(
                    "**YOUR GUIDE TO KY BOT**\nThoughtful tools for your community. "
                    "Choose a guide to get started."
                )
            )
            entries = [
                (
                    "ASK TO DM/FR",
                    "Send a request, respond privately and manage consent.",
                    "requests",
                ),
                (
                    "CHECKLISTS",
                    "Create order with tasks, inline actions and discussions.",
                    "checklists",
                ),
                (
                    "LEVELING & REWARDS",
                    "Understand XP, level progress and server rewards.",
                    "leveling",
                ),
                (
                    "QUICK REFERENCE",
                    "Essential commands and help with expired controls.",
                    "essentials",
                ),
            ]
            if self.administrator:
                entries.append(
                    ("COMMAND CENTER", "Channels, roles, logs and feature settings.", "admin")
                )
            if self.manage_guild:
                entries.append(
                    ("AUTOMATION & SETUP", "Channel automations and checklist setup.", "automation")
                )
            for title, description, target in entries:
                panel.add_item(
                    discord.ui.Section(
                        f"**{title}**\n-# {description}",
                        accessory=self.navigation("View guide", target),
                    )
                )
        else:
            title, sections = GUIDES[page]
            panel.add_item(discord.ui.TextDisplay(f"**{title}**"))
            for index, (heading, text) in enumerate(sections):
                if index:
                    panel.add_item(discord.ui.Separator())
                panel.add_item(discord.ui.TextDisplay(f"**{heading}**\n{text}"))
        section_dividers(panel)
        if page != "home":
            panel.add_item(
                discord.ui.Section(
                    "**HELP CENTER**\n-# Explore another guide.",
                    accessory=self.navigation("All guides", "home"),
                )
            )
        panel.add_item(
            discord.ui.Section(
                "**YOUR PRIVATE GUIDE**\n-# Only you can use these controls.",
                accessory=self.button("Close", self.close_menu),
            )
        )
        panel.add_item(
            discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://ky_footer_help.png"))
        )
        self.add_item(panel)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or interaction.guild_id != self.guild_id:
            await send_private(interaction, "Run `/help` to open your own private guide.")
            return False
        self.administrator = interaction.permissions.administrator
        self.manage_guild = interaction.permissions.manage_guild or self.administrator
        return True

    async def close_menu(self, interaction):
        closed = discord.ui.LayoutView(timeout=1)
        closed.add_item(discord.ui.TextDisplay("Help closed. Run `/help` whenever you need it."))
        await interaction.response.edit_message(view=closed, attachments=[])
        closed.stop()
        self.stop()

    async def on_timeout(self):
        for item in self.walk_children():
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                log.debug("Expired help message could not be updated.")

    async def on_error(self, interaction, error, item):
        await report_error(interaction, error)
