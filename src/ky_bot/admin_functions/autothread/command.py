"""Review-first /autothread menus; no defaults activate channels."""

import asyncio
import copy

import discord
from discord import app_commands
from discord.ext import commands

from ky_bot.admin_functions.autothread.service import (
    AutothreadService,
    check_permissions,
    starter_embed,
)
from ky_bot.admin_functions.autothread.store import (
    ARCHIVES,
    COLORS,
    CONTENT,
    STARTER,
    RuleStore,
    defaults,
    validate,
)
from ky_bot.checklists.board import safe
from ky_bot.checklists.inline import InlineView
from ky_bot.errors import report_error
from ky_bot.views.private_style import private_heading


def artwork(design=None):
    # dark_silver_icon.png is an overlay, never a complete footer banner.
    from ky_bot.design.menu_design import default_profile, menu_files

    return menu_files(
        design or default_profile("autothread"),
        "AUTOTHREAD",
        "CHANNEL AUTOMATION",
        ("ky_header_autothread.png", "dark_silver_icon.png", "ky_autothread_title.png"),
    )


class Session:
    def __init__(self, cog, interaction):
        self.cog, self.guild, self.owner = cog, interaction.guild, interaction.user.id
        self.channels = (
            [interaction.channel_id] if isinstance(interaction.channel, discord.TextChannel) else []
        )
        self.draft = defaults()
        self.original = None
        self.generation = 0
        self.lock = asyncio.Lock()
        self.design = None

    async def allowed(self, interaction, generation):
        p = interaction.user.guild_permissions
        if (
            interaction.guild_id == self.guild.id
            and interaction.user.id == self.owner
            and (p.administrator or p.manage_guild)
            and generation == self.generation
        ):
            return True
        await interaction.response.send_message(
            "Reopen /autothread with server management permissions.", ephemeral=True
        )
        return False

    async def show(self, interaction, page="main", rules_page=0):
        _, profiles = await self.cog.bot.server_settings.repository.get_designs(self.guild.id)
        from ky_bot.design.menu_design import profile_for

        self.design = profile_for(profiles, "autothread") if "autothread" in profiles else None
        self.generation += 1
        view = Menu(self, page, rules_page)
        uploads = (
            await asyncio.to_thread(artwork, self.design)
            if page in ("main", "configure", "rules", "all")
            else []
        )
        try:
            await interaction.edit_original_response(
                content=None, embeds=[], view=view, attachments=uploads
            )
        finally:
            for upload in uploads:
                upload.close()

    def validate_channels(self, *, enabling=True):
        if not self.channels:
            raise ValueError("Choose at least one destination channel.")
        channels = []
        member = self.guild.get_member(self.owner)
        for cid in self.channels:
            channel = self.guild.get_channel(cid)
            if (
                not isinstance(channel, discord.TextChannel)
                or not member
                or not channel.permissions_for(member).view_channel
            ):
                raise ValueError("Choose accessible text channels in this server.")
            if enabling:
                check_permissions(channel, self.draft["media_only"])
                if not channel.permissions_for(self.guild.me).manage_threads:
                    raise ValueError("KY BOT needs Manage Threads for inactivity archiving.")
            channels.append(channel)
        return channels


class WelcomeModal(discord.ui.Modal, title="Autothread welcome message"):
    def __init__(self, session):
        super().__init__(timeout=300)
        self.session, self.generation = session, session.generation
        self.text = discord.ui.TextInput(
            label="Message ({user} / {display_name})",
            default=session.draft["starter_message"],
            style=discord.TextStyle.paragraph,
            max_length=2000,
        )
        self.add_item(self.text)

    async def on_submit(self, interaction):
        if not await self.session.allowed(interaction, self.generation):
            return
        await interaction.response.defer()
        self.session.draft["starter_message"] = str(self.text)
        await self.session.show(interaction, "welcome")

    async def on_error(self, interaction, error):
        await report_error(interaction, error)


class Menu(discord.ui.LayoutView):
    def __init__(self, session, page="main", rules_page=0):
        super().__init__(timeout=600)
        self.session, self.page, self.generation = session, page, session.generation
        from ky_bot.design.collection import COLOURS

        colour = int(COLOURS[session.design["style"]][1:], 16) if session.design else 0xBDC3CA
        panel = discord.ui.Container(accent_colour=colour)
        main = page in ("main", "configure", "rules", "all")
        if main:
            for filename in ("ky_header_autothread.png", "ky_autothread_title.png"):
                panel.add_item(
                    discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://" + filename))
                )
        else:
            panel.add_item(
                discord.ui.TextDisplay(
                    private_heading(
                        {
                            "channels": "DESTINATION",
                            "content": "CONTENT & TRIGGERS",
                            "uploads": "ATTACHMENT RULES",
                            "archive": "AUTO-ARCHIVE",
                            "access": "ACCESS & FILTERS",
                            "welcome": "WELCOME MESSAGE",
                            "review": "PREVIEW & SAVE",
                            "toggle": "TURN ON / OFF",
                            "delete": "REMOVE RULE",
                        }.get(page, "AUTOTHREAD")
                    )
                )
            )

        def row(title, description, label, target=None, callback=None):
            button = discord.ui.Button(label=label)

            async def click(interaction):
                await interaction.response.defer()
                await session.show(interaction, target)

            button.callback = callback or click
            panel.add_item(discord.ui.Section(f"**{title}**\n-# {description}", accessory=button))

        def picker(select, callback):
            async def choose(interaction):
                await interaction.response.defer()
                await callback(select.values, interaction)

            select.callback = choose
            panel.add_item(discord.ui.ActionRow(select))

        destinations = ", ".join(f"<#{c}>" for c in session.channels) or "No channels selected"
        if page == "main":
            row("DESTINATION", destinations, "Choose", "channels")
            row(
                "CONFIGURE RULES",
                "Triggers, attachment requirements and thread settings.",
                "Configure",
                "configure",
            )
            row("SAVED RULES", "Edit rules in selected channels.", "Manage", "rules")
            row("SERVER RULES", "Browse all configured channels.", "View all", "all")
            row("TURN ON / OFF", "Resume or pause existing rules.", "Turn on / off", "toggle")
        elif page == "configure":
            row("DESTINATION", destinations, "Change", "channels")
            row(
                "CONTENT & TRIGGERS",
                ", ".join(CONTENT[c] for c in session.draft["content"]) or "Choose content.",
                "Choose",
                "content",
            )
            row(
                "ATTACHMENT RULES",
                "Upload requirements and media-only enforcement.",
                "Edit",
                "uploads",
            )
            row(
                "AUTO-ARCHIVE",
                ARCHIVES[session.draft["archive"]] + " of inactivity",
                "Edit",
                "archive",
            )
            row(
                "ACCESS & FILTERS",
                "Ignore webhooks: " + ("On" if session.draft["ignore_webhooks"] else "Off"),
                "Edit",
                "access",
            )
            row("WELCOME MESSAGE", "Message, placeholders and embed color.", "Edit", "welcome")
            row("PREVIEW & SAVE", "Review before applying to future posts.", "Preview", "review")
            if session.original:
                row(
                    "REMOVE RULE",
                    "Remove this rule without deleting posts or threads.",
                    "Review",
                    "delete",
                )
        elif page == "channels":

            async def select_channels(values, interaction):
                session.channels = [c.id for c in values]
                session.original = None
                await session.show(interaction, "configure")

            picker(
                discord.ui.ChannelSelect(
                    channel_types=[discord.ChannelType.text], min_values=1, max_values=25
                ),
                select_channels,
            )
        elif page == "content":
            options = {
                k: v
                for k, v in CONTENT.items()
                if not session.draft["media_only"] or k in ("image", "video")
            }

            async def select_content(values, interaction):
                session.draft["content"] = list(values)
                await session.show(interaction, "configure")

            picker(
                discord.ui.Select(
                    placeholder="Choose content types",
                    min_values=1,
                    max_values=len(options),
                    options=[
                        discord.SelectOption(
                            label=v, value=k, default=k in session.draft["content"]
                        )
                        for k, v in options.items()
                    ],
                ),
                select_content,
            )
        elif page in ("uploads", "access"):
            for key, label in (
                (("require_attachment", "REQUIRE ATTACHMENT"), ("media_only", "MEDIA-ONLY CHANNEL"))
                if page == "uploads"
                else (("ignore_webhooks", "IGNORE WEBHOOKS"),)
            ):

                async def flip(interaction, key=key):
                    await interaction.response.defer()
                    if key == "require_attachment" and session.draft["media_only"]:
                        await interaction.followup.send(
                            "Media-only enforcement requires uploaded photos/videos.",
                            ephemeral=True,
                        )
                        return
                    session.draft[key] = not session.draft[key]
                    if session.draft["media_only"]:
                        session.draft["require_attachment"] = True
                        session.draft["content"] = [
                            c for c in session.draft["content"] if c in ("image", "video")
                        ]
                    await session.show(interaction, page)

                row(label, "On" if session.draft[key] else "Off", "Toggle", callback=flip)
            if page == "uploads":
                panel.add_item(
                    discord.ui.TextDisplay(
                        "-# Media-only enforcement deletes invalid parent posts, "
                        "including GIFs, and shows a 10-second warning."
                    )
                )
        elif page == "archive":

            async def archive(values, interaction):
                session.draft["archive"] = int(values[0])
                await session.show(interaction, "configure")

            picker(
                discord.ui.Select(
                    options=[
                        discord.SelectOption(
                            label=v, value=str(k), default=k == session.draft["archive"]
                        )
                        for k, v in ARCHIVES.items()
                    ]
                ),
                archive,
            )
        elif page == "welcome":

            async def color(values, interaction):
                session.draft["embed_color"] = None if values[0] == "default" else int(values[0])
                await session.show(interaction, "welcome")

            picker(
                discord.ui.Select(
                    options=[discord.SelectOption(label="Category default", value="default")]
                    + [discord.SelectOption(label=k, value=str(v)) for k, v in COLORS.items()]
                ),
                color,
            )

            async def edit(interaction):
                await interaction.response.send_modal(WelcomeModal(session))

            async def restore(interaction):
                await interaction.response.defer()
                session.draft["starter_message"] = STARTER
                await session.show(interaction, "welcome")

            async def preview(interaction):
                channel = (
                    session.guild.get_channel(session.channels[0])
                    if session.channels
                    else interaction.channel
                )
                await interaction.response.send_message(
                    embed=starter_embed(session.draft, interaction.user, channel),
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            row("MESSAGE", safe(session.draft["starter_message"])[:700], "Edit", callback=edit)
            row("DEFAULT MESSAGE", "Restore the original welcome.", "Restore", callback=restore)
            row("PREVIEW", "Use your profile to preview the welcome.", "Preview", callback=preview)
        elif page in ("rules", "all"):
            rules = [
                r
                for r in session.cog.store.list(session.guild.id)
                if page == "all" or r["channel"] in session.channels
            ]
            if not rules:
                panel.add_item(discord.ui.TextDisplay("No saved rules."))
                row("NEW RULE", destinations, "Configure", "configure")
            for rule in rules[rules_page * 4 : rules_page * 4 + 4]:

                async def edit_rule(interaction, rule=rule):
                    await interaction.response.defer()
                    session.original = copy.deepcopy(rule)
                    session.channels = [rule["channel"]]
                    session.draft = {k: rule[k] for k in defaults()}
                    await session.show(interaction, "configure")

                row(
                    f"RULE #{rule['id']}",
                    f"<#{rule['channel']}> · {'On' if rule['enabled'] else 'Paused'} · "
                    f"{ARCHIVES[rule['archive']]}",
                    "Edit",
                    callback=edit_rule,
                )
            if len(rules) > 4:
                for label, offset in (("Previous", -1), ("Next", 1)):
                    button = discord.ui.Button(
                        label=label, disabled=not 0 <= rules_page + offset < (len(rules) + 3) // 4
                    )

                    async def turn(interaction, offset=offset):
                        await interaction.response.defer()
                        await session.show(interaction, page, rules_page + offset)

                    button.callback = turn
                    panel.add_item(discord.ui.ActionRow(button))
        elif page == "toggle":
            for enabled in (True, False):

                async def toggle(interaction, enabled=enabled):
                    await interaction.response.defer()
                    async with session.lock:
                        session.validate_channels(enabling=False)
                        if enabled:
                            for rule in session.cog.store.list(session.guild.id):
                                if rule["channel"] in session.channels:
                                    check_permissions(
                                        session.guild.get_channel(rule["channel"]),
                                        rule["media_only"],
                                    )
                                    if (
                                        not session.guild.get_channel(rule["channel"])
                                        .permissions_for(session.guild.me)
                                        .manage_threads
                                    ):
                                        raise ValueError("KY BOT needs Manage Threads.")
                        count = session.cog.store.toggle(
                            session.guild.id, session.channels, enabled
                        )
                    await interaction.followup.send(
                        f"{count} rules {'enabled' if enabled else 'paused'}.", ephemeral=True
                    )

                row(
                    "TURN ON" if enabled else "TURN OFF",
                    destinations,
                    "Turn on" if enabled else "Turn off",
                    callback=toggle,
                )
        elif page == "delete":

            async def remove(interaction):
                await interaction.response.defer()
                async with session.lock:
                    session.validate_channels(enabling=False)
                    if session.original:
                        session.cog.store.delete(session.guild.id, session.original["id"])
                    session.original, session.draft = None, defaults()
                await session.show(interaction, "rules")

            row(
                "REMOVE RULE",
                "Existing messages and discussions will remain.",
                "Remove",
                callback=remove,
            )
        elif page == "review":
            panel.add_item(
                discord.ui.TextDisplay(
                    f"**DESTINATION**\n{destinations}\n**CONTENT**\n"
                    f"{', '.join(session.draft['content']) or 'None'}\n"
                    f"**ARCHIVE**\n{ARCHIVES[session.draft['archive']]}\n"
                    f"**REQUIRE ATTACHMENT** · {session.draft['require_attachment']}\n"
                    f"**MEDIA-ONLY** · {session.draft['media_only']}\n"
                    f"**IGNORE WEBHOOKS** · {session.draft['ignore_webhooks']}\n"
                    f"**WELCOME**\n{safe(session.draft['starter_message'])[:1500]}"
                )
            )

            async def save(interaction):
                await interaction.response.defer()
                async with session.lock:
                    session.validate_channels()
                    validate(session.draft)
                    session.cog.store.save_configuration(
                        session.guild.id, session.channels, session.draft, session.original
                    )
                    session.original = None
                    session.channels = []
                    session.draft = defaults()
                await session.show(interaction)
                await interaction.followup.send(
                    "AutoThread rules saved. Only new qualifying posts trigger threads.",
                    ephemeral=True,
                )

            row("SAVE RULE", "Apply this reviewed draft.", "Save", callback=save)
        panel.add_item(
            discord.ui.TextDisplay(
                "-# Private admin controls · Draft changes apply only after Save"
            )
        )
        if main:
            panel.add_item(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem("attachment://dark_silver_icon.png")
                )
            )
        back = discord.ui.Button(label="Back")

        async def go_back(interaction):
            await interaction.response.defer()
            await session.show(
                interaction,
                "main" if page in ("main", "configure", "rules", "all") else "configure",
            )

        back.callback = go_back
        close = discord.ui.Button(label="Close" if page == "main" else "Cancel setup")

        async def cancel(interaction):
            await interaction.response.defer()
            session.draft, session.original = defaults(), None
            if page == "main":
                await interaction.edit_original_response(
                    content=None,
                    attachments=[],
                    view=InlineView().add_item(discord.ui.TextDisplay("AutoThread menu closed.")),
                )
            else:
                await session.show(interaction)

        close.callback = cancel
        panel.add_item(
            discord.ui.ActionRow(close) if page == "main" else discord.ui.ActionRow(back, close)
        )
        self.add_item(panel)

    async def interaction_check(self, interaction):
        return await self.session.allowed(interaction, self.generation)

    async def on_error(self, interaction, error, item):
        if isinstance(error, ValueError):
            if interaction.response.is_done():
                await interaction.followup.send(str(error), ephemeral=True)
            else:
                await interaction.response.send_message(str(error), ephemeral=True)
        else:
            await report_error(interaction, error)


class AutoThread(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if bot.settings.database_url:
            from ky_bot.admin_functions.autothread.postgres import PostgresRuleStore

            self.store = PostgresRuleStore(bot.settings.database_url)
        else:
            path = bot.settings.database_path
            self.store = RuleStore(
                ":memory:" if str(path) == ":memory:" else path.with_name("ky-autothread.sqlite3")
            )
        self.service = AutothreadService(bot, self.store)

    async def cog_load(self):
        self.service.start()

    async def cog_unload(self):
        await self.service.close()
        self.store.db.close()

    @commands.Cog.listener()
    async def on_message(self, message):
        await self.service.on_message(message)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        await self.service.on_raw_message_edit(payload)

    @app_commands.command(
        name="autothread", description="Configure automatic threads for server channels."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def autothread(self, interaction):
        await self.open_menu(interaction)

    async def open_menu(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        session = Session(self, interaction)
        _, profiles = await self.bot.server_settings.repository.get_designs(interaction.guild_id)
        from ky_bot.design.menu_design import profile_for

        session.design = profile_for(profiles, "autothread") if "autothread" in profiles else None
        uploads = await asyncio.to_thread(artwork, session.design)
        try:
            await interaction.edit_original_response(view=Menu(session), attachments=uploads)
        finally:
            for upload in uploads:
                upload.close()


async def setup(bot):
    await bot.add_cog(AutoThread(bot))
