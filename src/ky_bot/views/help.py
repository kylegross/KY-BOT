"""Private help center using KY BOT's dark silver artwork and inline navigation."""

import io
import logging
from importlib.resources import files

import discord

from ky_bot.errors import report_error, send_private
from ky_bot.views.media import NoAltMediaGalleryItem
from ky_bot.views.menu_strips import add_strip, category_files, group_sections, strip_files
from ky_bot.views.sections import section_dividers

log = logging.getLogger(__name__)

GUIDES = {
    "nsfw": (
        "NSFW FLASH LOGS",
        (
            (
                "AGE RESTRICTION & SPOILERS",
                "`/nsfw toggle` toggles Discord's Age-Restricted setting in "
                "the current text channel. Optional channel, enabled, logs, "
                "log_channel and require_spoilers "
                "options let administrators configure it directly. Command Center → Logs → "
                "NSFW Flash Logs offers the same controls. Spoiler enforcement "
                "removes whole unspoilered "
                "media posts and asks the author to repost; bot removals are "
                "suppressed in deletion logs.",
            ),
            (
                "TEXT-ONLY CHANNEL LOGS",
                "Choose source and separate age-restricted staff log channels, "
                "then select text/captions, media notices, edit/delete events, "
                "reaction removals/clears "
                "or provider GIF links. **Images/videos, filenames, direct media "
                "URLs and previews are not saved.**",
            ),
            (
                "GROUPING & STORAGE",
                "One-minute member/conversation summaries update in place; long "
                "summaries use continuation embeds. KY BOT AutoDelete removals are "
                "suppressed, including "
                "the five-minute cleanup timing rule. Posted logs remain in "
                "Discord. Confirmed delivered "
                "payload copies are compacted after two minutes; retry payloads, receipts and live "
                "source snapshots for edits/deletions remain available.",
            ),
            (
                "SERVER-EVENT EXCLUSIONS",
                "Server Events settings include a channel/category exclusion "
                "dropdown. Threads inherit parent/category exclusions. This excludes "
                "server-event logs, "
                "while separately enabled NSFW Flash Logs keep their own configuration.",
            ),
        ),
    ),
    "requests": (
        "ASK TO DM/FR",
        (
            (
                "ASK FIRST",
                "Open your server’s **Ask to DM/FR** channel and select **Ask to DM/FR**. "
                "Choose a member, request type and optional private reason, then confirm. Wait "
                "for acceptance before contacting them.",
            ),
            (
                "YOUR REQUESTS",
                "Select **My requests** on the channel panel to respond, cancel an unanswered "
                "request or withdraw permission you previously gave. You can also use the "
                "buttons in KY BOT’s private notifications.",
            ),
            (
                "PRIVACY & CONSENT",
                "Recipients can accept or decline. Unanswered requests expire after **48 "
                "hours**. Reasons stay private; staff can review request logs. When DMs are "
                "blocked, delivery uses a private thread accessible to that member and staff "
                "with Manage Threads.",
            ),
            (
                "SERVER RULES & STICKY MENU",
                "Your server controls sender and recipient role restrictions and whether "
                "request cards appear publicly. Staff logs update the original request record "
                "when its status changes. When public requests are enabled, the entry menu "
                "moves to the bottom and removes its old copy. Hidden-request mode leaves the "
                "entry menu in place.",
            ),
        ),
    ),
    "checklists": (
        "CHECKLISTS",
        (
            (
                "ADD A TASK",
                "In a configured checklist channel, an authorized member’s message becomes a "
                "task. KY BOT saves the task and its attachments before removing the "
                "original message.",
            ),
            (
                "MANAGE YOUR BOARD",
                "Use the inline **Manage** button beside a task to access its available "
                "actions. Depending on your permissions, you can edit or reorder tasks, mark "
                "them complete and open a private discussion thread.",
            ),
            (
                "DELETE A CHECKLIST",
                "Server administrators can select **Delete checklist** at the bottom of the "
                "board, then confirm in the private prompt. This removes the board and its "
                "tasks, sub-tasks and categories. The channel, posted logs and discussion "
                "threads are kept.",
            ),
            (
                "WHO CAN USE IT",
                "Task actions follow the board’s administrator and authorized-role rules. "
                "Ask a server administrator for access or to configure a checklist.",
            ),
        ),
    ),
    "leveling": (
        "LEVELING & REWARDS",
        (
            (
                "EARN XP",
                "When enabled by your server, eligible messages earn XP. Voice XP is optional. "
                "Cooldowns, excluded channels and excluded roles determine which activity "
                "counts.",
            ),
            (
                "LEVEL UP",
                "Your server can configure level rewards and member rankings. Available "
                "rewards depend on the roles and settings chosen by administrators.",
            ),
            (
                "NEED AN ADJUSTMENT?",
                "Ask an administrator to review your XP. Member corrections are restricted to "
                "server administrators.",
            ),
        ),
    ),
    "confessions": (
        "CONFESSIONS",
        (
            (
                "SUBMIT PRIVATELY",
                "Use `/confess` or Submit confession on a channel’s panel. "
                "Choose a configured destination, write your message and review the privacy "
                "notice before confirming. Your name and avatar never appear on the public post.",
            ),
            (
                "APPROVAL & AUTHOR LOGS",
                "Channels can require administrator approval. In that "
                "mode, administrators see the author in the private queue even when ongoing author "
                "logging is off. Optional author logs also name the sender "
                "in a private log channel. "
                "The confirmation tells you which settings apply before you submit.",
            ),
            (
                "REPLY OR REPORT",
                "Use Reply anonymously to respond, or Report to privately tell "
                "administrators about a confession. Replies follow the channel’s approval and "
                "logging settings. Reports identify the reporter only to administrators.",
            ),
            (
                "CHANNEL SETTINGS",
                "Administrators use Command Center → Confessions to configure "
                "multiple channels independently: header title, embed color, artwork finish, "
                "four lettering finishes or a custom text color, uploaded header/footer artwork, "
                "panel text, "
                "replies, reports, role access, cooldown, character limits and confession bans.",
            ),
            (
                "STICKY PANEL & LOGS",
                "Sticky settings move only the submission panel after the "
                "configured number of member messages. Confessions stay in order. "
                "Logs → Other Logs "
                "→ Confessions opens the same per-channel log and administrator-review settings.",
            ),
        ),
    ),
    "essentials": (
        "QUICK REFERENCE",
        (
            (
                "HELP ANYWHERE",
                "`/help` works with a server or personal installation. Only you can use its "
                "controls. Server features still require KY BOT in the relevant server.",
            ),
            ("CHECK KY BOT", "`/ping` checks responsiveness and heartbeat latency in a server."),
            (
                "PERSONAL INSTALLATION",
                "Personally installed `/steal` commands work across supported servers and "
                "private conversations. Use `/steal set` to choose a destination where you "
                "and KY BOT can create expressions.",
            ),
            (
                "CONTROLS EXPIRED?",
                "Run `/help` again. For other expired forms, open a new form from the "
                "feature’s channel panel. Finished copies are not removed when a picker "
                "expires.",
            ),
        ),
    ),
    "admin": (
        "COMMAND CENTER",
        (
            (
                "SERVER CONFIGURATION",
                "Run `/settings` as a server administrator. Choose a **Command Center channel** "
                "to keep a permanent management panel in your server.",
            ),
            (
                "ASK TO DM/FR",
                "Choose request and staff log channels, set sender and recipient role "
                "permissions, and choose whether requests appear in the channel. Select **Publish "
                "/ refresh**, then **Enable**.",
            ),
            (
                "MEMBER EXPERIENCE",
                "Configure welcomes, Leveling, server activity logs and embed design from their "
                "Command Center sections.",
            ),
            (
                "CREATE CUSTOM EMBEDS",
                "Open **Command Center → Manage Embeds**. Add images, text, a highlight color and "
                "optional footer. Preview privately, then publish. Sticky posts can move after "
                "every message or a chosen message count; their old copy is removed. Saved drafts "
                "do not change live posts until you publish. Menu appearance settings are under "
                "General Settings.",
            ),
            (
                "MEMBER MODERATION",
                "Open **Command Center → Moderation** for a private member picker. Or use `/ban "
                "member`, `/kick member` and `/timeout member duration`. "
                "Timeout examples: `30m`, `2h`, `3d 12h`. "
                "Maximum: **28 days**. Kick Members, Ban Members or Moderate Members "
                "permissions are required, "
                "and actions show a confirmation before applying.",
            ),
            (
                "LEVEL-UP FORMATTING & XP",
                "**Leveling → Level-Up Messages → Edit formatting** changes the heading, "
                "announcement message, card height and text sizes. Message placeholders: "
                "`{mention}`, `{name}`, `{level}`. Select **Preview** "
                "to check the card privately.\n"
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
                "Open **Command Center → General Settings** to manage Autodelete, Autothread "
                "and Autoreact. Server administrators manage these through the Command "
                "Center.",
            ),
            (
                "AUTOMATION COMMANDS",
                "`/purge` opens one-time cleanup in the current channel. Select content, "
                "timeframe and scan limit, then Review → Delete now. "
                "It does not create an automatic rule.\n"
                "`/autodelete` · `/autothread` · `/autoreact`\n"
                "These configuration commands require **Manage Server**. Review the selected "
                "channels, permissions and rules before enabling automation.",
            ),
            (
                "CREATE A CHECKLIST",
                "`/create checklist`\n"
                "Requires **Manage Server**. Choose Yes for a separate checklist log channel "
                "or No to create without one. An authorized role and custom title are "
                "optional.",
            ),
        ),
    ),
    "expressions": (
        "EMOJIS & STICKERS",
        (
            (
                "SET YOUR DESTINATION",
                "`/steal set server` saves your destination for your account. Choose a "
                "server from autocomplete or enter its ID. You and KY BOT need Create "
                "Expressions or Manage Expressions there. KY BOT must be installed in that "
                "server.",
            ),
            (
                "COPY EMOJIS",
                "`/steal emoji source name` accepts one or more custom emojis or a message "
                "link. The name is optional for one emoji; otherwise the original is kept. "
                "Static and animated emojis retain their animation format. Standard Unicode "
                "emojis need no copying.",
            ),
            (
                "COPY STICKERS",
                "`/steal sticker source name` accepts a sticker ID, Discord sticker link or "
                "message link. PNG, APNG and GIF keep their format. For ordinary servers, "
                "Lottie animations become animated APNG copies, never still images. "
                "Discord size, duration and server-slot limits still apply.",
            ),
            (
                "SELECT BY REPLY",
                "Run `/steal emoji` or `/steal sticker` without source, then reply to the "
                "original message with `copy` within 60 seconds. KY BOT must be installed "
                "in that source server. The picker opens privately; you still choose what "
                "to copy. Slash commands do not receive an already-selected reply target; "
                "use the message’s Apps action for one-step selection or personal-only installs.",
            ),
            (
                "CHECK THE ITEMS YOU WANT",
                "A private multi-select popup lists the assets individually. All begin "
                "checked. Uncheck any to leave behind, use pages for long lists and Rename "
                "one for individual custom names. Nothing is uploaded until you select Copy "
                "selected.",
            ),
            (
                "FROM OTHER SERVERS",
                "With a personal installation, right-click or long-press the original "
                "message → Apps → Steal emojis / stickers. Discord provides that message "
                "directly. A personal installation does not grant access to other message "
                "history, and cannot upload custom assets directly to your personal "
                "account.",
            ),
        ),
    ),
    "moderation": (
        "MEMBER MODERATION",
        (
            (
                "BAN A MEMBER",
                "`/ban member reason` requires Ban Members. A private review names the "
                "target before you confirm. Existing message history is kept; the ban "
                "remains until unbanned.",
            ),
            (
                "KICK A MEMBER",
                "`/kick member reason` requires Kick Members. The reason is optional. "
                "Review privately, then confirm to remove the member. They can rejoin "
                "with a valid invite. Existing messages are kept; the reason is recorded "
                "in Discord’s audit log.",
            ),
            (
                "CUSTOM TIMEOUT",
                "`/timeout member duration reason` requires Moderate Members. Enter `30m`, "
                "`2h`, `3d 12h` or `1 hour 30 minutes`. Maximum: **28 days**. The optional "
                "reason is recorded in Discord’s audit log, and the timeout ends "
                "automatically.",
            ),
            (
                "PERMISSIONS & HIERARCHY",
                "KY BOT must have the relevant permission, and both your role and its role "
                "must be above the target. You cannot target yourself, KY BOT or the server "
                "owner. Bots and Administrator members cannot be timed out.",
            ),
            (
                "COMMAND CENTER",
                "Server administrators can also open Command Center → Moderation for private "
                "member pickers and custom-time entry. Every action is reviewed and "
                "permissions are checked again when confirmed.",
            ),
        ),
    ),
    "embeds": (
        "CUSTOM EMBEDS",
        (
            (
                "CREATE OR EDIT",
                "Run `/create embed` or open Command Center → Manage Embeds. Start a new "
                "saved draft or choose an "
                "existing post. Add a title, message and optional footer text.",
            ),
            (
                "IMAGES, COLOR & FORMATTING",
                "Upload a header and/or footer image, choose an accent color preset or custom "
                "hex. Content & Text Styles puts a style dropdown above each text field and "
                "Add styled text at the top. Titles start as large headings. Images are saved for "
                "later reposts; role and everyone notifications are suppressed.",
            ),
            (
                "PREVIEW THEN PUBLISH",
                "Previews are private. Draft changes stay separate from the live message until "
                "Publish. Publishing updates the known copy or moves it to a new channel.",
            ),
            (
                "STICKY DELIVERY",
                "Sticky Off leaves the post in place. A count of 1 moves after every new "
                "message; a larger count waits that many messages. Each move removes the old "
                "copy. KY BOT’s own posts do not count. Counters and saved content survive "
                "restarts.",
            ),
            (
                "DELETE",
                "Use Preview & Publish → Delete and confirm to stop sticky delivery and remove "
                "the saved content and known posted copies. Other messages and the channel "
                "stay.",
            ),
        ),
    ),
    "server": (
        "WELCOME, LOGS & APPEARANCE",
        (
            (
                "WELCOME EXPERIENCE",
                "Use Command Center → Welcome Experience to choose a channel, edit the greeting "
                "and customize the welcome image. Preview changes before using them.",
            ),
            (
                "SERVER ACTIVITY",
                "Open Logs → Server Activity for server-event and member-log channels. The bot can "
                "record messages, members, roles, bans and invites. Channel permissions "
                "determine who can view those logs.",
            ),
            (
                "MENU APPEARANCE",
                "Command Center → Logs → Menu Appearance manages the shared design "
                "of KY BOT menus. Preview the selected finishes and apply them together.",
            ),
            (
                "OTHER LOGS",
                "Logs → Other Logs collects feature logging shortcuts. Checklist logs are also "
                "selected during checklist setup. Ask to DM/FR has its "
                "own staff log channel and public/hidden request switch. Changing request "
                "visibility does not turn staff logging off.",
            ),
        ),
    ),
}


def _base_help_files():
    return [
        discord.File(
            io.BytesIO(files("ky_bot.design").joinpath("assets", directory, name).read_bytes()),
            filename=name,
        )
        for directory, name in (("header", "ky_header_help.png"), ("footer", "ky_footer_help.png"))
    ]


class HelpView(discord.ui.LayoutView):
    def __init__(
        self,
        owner_id,
        guild_id,
        *,
        administrator=False,
        manage_guild=False,
        ban_members=False,
        kick_members=False,
        moderate_members=False,
    ):
        super().__init__(timeout=600)
        self.owner_id, self.guild_id = owner_id, guild_id
        self.administrator = administrator
        self.manage_guild = manage_guild or administrator
        self.moderator = administrator or ban_members or kick_members or moderate_members
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
            attachments = help_files(self)
            try:
                self.message = await interaction.edit_original_response(
                    view=self, attachments=attachments
                )
            finally:
                for attachment in attachments:
                    attachment.close()

        return self.button(label, navigate)

    def show(self, page):
        if page in {"admin", "embeds", "server", "nsfw"} and not self.administrator:
            page = "home"
        if page == "automation" and not self.manage_guild:
            page = "home"
        if page == "moderation" and not self.moderator:
            page = "home"
        self.page = page
        self.clear_items()
        panel = discord.ui.Container(accent_colour=0xD6E2F3)
        panel.add_item(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://ky_header_help.png"))
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
                    "CONFESSIONS",
                    "Anonymous posts and replies, privacy notices and reporting.",
                    "confessions",
                ),
                (
                    "LEVELING & REWARDS",
                    "Understand XP, level progress and server rewards.",
                    "leveling",
                ),
                (
                    "EMOJIS & STICKERS",
                    "Copy posted assets with batch selection and custom names.",
                    "expressions",
                ),
                (
                    "QUICK REFERENCE",
                    "Essential commands and help with expired controls.",
                    "essentials",
                ),
            ]
            if self.moderator:
                entries.append(
                    (
                        "MEMBER MODERATION",
                        "Kicks, bans, custom timeouts and role checks.",
                        "moderation",
                    )
                )
            if self.administrator:
                entries.append(
                    (
                        "NSFW FLASH LOGS",
                        "Age restriction, spoiler policy and private text-only logs.",
                        "nsfw",
                    )
                )
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
            for heading, text in sections:
                add_strip(panel, heading)
                panel.add_item(discord.ui.TextDisplay(text))
        if page == "admin":
            for label, target in (
                ("CUSTOM EMBEDS", "embeds"),
                ("WELCOME, LOGS & APPEARANCE", "server"),
            ):
                panel.add_item(
                    discord.ui.Section(
                        f"**{label}**", accessory=self.navigation("View guide", target)
                    )
                )
        if page != "home":
            panel.add_item(
                discord.ui.Section(
                    "**BACK**\n-# Return to the previous guide.",
                    accessory=self.navigation(
                        "Back", "admin" if page in {"embeds", "server"} else "home"
                    ),
                )
            )
        panel.add_item(
            discord.ui.Section(
                "**YOUR PRIVATE GUIDE**\n-# Only you can use these controls.",
                accessory=self.button("Close", self.close_menu),
            )
        )
        panel.add_item(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://ky_footer_help.png"))
        )
        if page == "home":
            group_sections(panel, "help")
        section_dividers(panel, navigation_on_right=True)
        self.add_item(panel)

    async def interaction_check(self, interaction):
        if interaction.user.id != self.owner_id or interaction.guild_id != self.guild_id:
            await send_private(interaction, "Run `/help` to open your own private guide.")
            return False
        self.administrator = bool(
            interaction.guild_id is not None and interaction.permissions.administrator
        )
        self.manage_guild = bool(
            interaction.guild_id is not None
            and (interaction.permissions.manage_guild or self.administrator)
        )
        self.moderator = bool(
            interaction.guild_id is not None
            and (
                interaction.permissions.ban_members
                or interaction.permissions.kick_members
                or interaction.permissions.moderate_members
                or self.administrator
            )
        )
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


def help_files(view=None):
    if view is not None and view.page != "home":
        return _base_help_files() + category_files(
            (heading for heading, _ in GUIDES[view.page][1]), "silver_neon"
        )
    return _base_help_files() + strip_files("help", "silver_neon")
