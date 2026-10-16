"""Inline /autodelete menus with explicit review before activation or cleanup."""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from ky_bot.checklists.board import safe
from ky_bot.design.menu_design import automation_files, automation_profile, default_profile
from ky_bot.errors import report_error
from ky_bot.views.private_style import private_heading

from .autodelete_logging import TYPES
from .autodelete_service import AutoDeleteService, check_permissions
from .autodelete_store import LABELS, PRESETS, RuleStore, normalize


def artwork(design=None):
    return automation_files(
        "autodelete",
        design,
        ("autodelete_header.png", "autodelete_footer.png", "autodelete_title.png"),
    )


class Session:
    def __init__(self, cog, interaction):
        self.cog, self.guild, self.owner = cog, interaction.guild, interaction.user.id
        self.channels = (
            [interaction.channel_id] if isinstance(interaction.channel, discord.TextChannel) else []
        )
        self.content, self.seconds, self.original = [], 300, None
        self.clean, self.window, self.scan = False, "within", 100
        self.log_destination, self.log_types = None, list(TYPES)
        self.log_title = "CHANNEL ACTIVITY LOG"
        self.design, self.generation, self.page = None, 0, 0
        self.lock = asyncio.Lock()
        self.command_center = None

    async def allowed(self, i, generation):
        p = i.user.guild_permissions
        if (
            i.user.id == self.owner
            and i.guild_id == self.guild.id
            and (p.manage_guild or p.administrator)
            and generation == self.generation
            and (self.command_center is None or i.permissions.administrator)
        ):
            return True
        await i.response.send_message(
            "Reopen /autodelete with Manage Server permission.", ephemeral=True
        )
        return False

    def destinations(self, keys=None):
        if not self.channels:
            raise ValueError("Select destination channels first.")
        member = self.guild.get_member(self.owner)
        result = []
        for cid in self.channels:
            channel = self.guild.get_channel(cid)
            if not isinstance(channel, discord.TextChannel) or not member:
                raise ValueError("Choose existing text channels in this server.")
            check_permissions(channel, keys or self.content or ["messages_all"], member)
            result.append(channel)
        return result

    async def show(self, i, page="main"):
        _, profiles = await self.cog.bot.server_settings.repository.get_designs(self.guild.id)
        self.design = automation_profile(profiles, "autodelete")
        self.generation += 1
        view = Menu(self, page)
        uploads = (
            await asyncio.to_thread(artwork, self.design)
            if page in ("main", "configure", "rules", "all", "clean")
            else []
        )
        try:
            await i.edit_original_response(
                content=None,
                embeds=[],
                view=view,
                attachments=uploads,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            for upload in uploads:
                upload.close()


class Input(discord.ui.Modal):
    def __init__(self, s, log=False):
        super().__init__(title="Activity log title" if log else "Custom timeframe")
        self.session, self.generation, self.log = s, s.generation, log
        self.value = discord.ui.TextInput(
            label="Title" if log else "Minutes (1–525600)",
            default=s.log_title if log else str(s.seconds // 60),
            max_length=80 if log else 6,
        )
        self.add_item(self.value)

    async def on_submit(self, i):
        s = self.session
        async with s.lock:
            if not await s.allowed(i, self.generation):
                return
            if self.log:
                value = str(self.value).strip()
                if not value or any(ord(c) < 32 for c in value):
                    raise ValueError("Enter a single-line log title.")
                s.log_title = value
            else:
                try:
                    minutes = int(str(self.value))
                except ValueError:
                    raise ValueError("Enter a whole number of minutes.") from None
                if not 1 <= minutes <= 525600:
                    raise ValueError("Use a timeframe from one minute to one year.")
                s.seconds = minutes * 60
            await i.response.defer()
            await s.show(i, "logs" if self.log else "time")

    async def on_error(self, i, error):
        await handle_error(i, error)


async def handle_error(i, error):
    if isinstance(error, ValueError):
        send = i.followup.send if i.response.is_done() else i.response.send_message
        await send(str(error), ephemeral=True)
    else:
        await report_error(i, error)


class Menu(discord.ui.LayoutView):
    def __init__(self, s, page="main"):
        super().__init__(timeout=600)
        self.session, self.generation = s, s.generation
        from ky_bot.design.collection import COLOURS

        panel = discord.ui.Container(
            accent_colour=int(COLOURS[(s.design or default_profile("autodelete"))["style"]][1:], 16)
        )
        main = page in ("main", "configure", "rules", "all", "clean")
        if main:
            for name in ("autodelete_header.png", "autodelete_title.png"):
                panel.add_item(
                    discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://" + name))
                )
        else:
            panel.add_item(
                discord.ui.TextDisplay(
                    private_heading(
                        {
                            "channels": "DESTINATION",
                            "content": "CONTENT & BOT MEDIA",
                            "time": "TIMEFRAME",
                            "toggle": "TURN ON / OFF",
                            "review": "REVIEW RULE",
                            "confirm_clean": "CONFIRM CLEANUP",
                            "remove": "REMOVE RULE",
                            "logs": "ACTIVITY LOGS",
                            "log_review": "REVIEW ACTIVITY LOGS",
                        }.get(page, "AUTODELETE")
                    )
                )
            )

        def row(title, description, label, target=None, action=None, disabled=False, modal=None):
            b = discord.ui.Button(label=label, disabled=disabled)

            async def click(i):
                async with s.lock:
                    if not await s.allowed(i, self.generation):
                        return
                    if modal is not None:
                        await i.response.send_modal(Input(s, modal))
                        return
                    await i.response.defer()
                    if action:
                        await action(i)
                    else:
                        if target in ("configure", "clean"):
                            s.clean = target == "clean"
                        s.page = 0
                        await s.show(i, target)

            b.callback = click
            panel.add_item(discord.ui.Section(f"**{title}**\n-# {description}", accessory=b))

        def picker(select, callback):
            async def choose(i):
                async with s.lock:
                    if not await s.allowed(i, self.generation):
                        return
                    await i.response.defer()
                    await callback(select.values, i)

            select.callback = choose
            panel.add_item(discord.ui.ActionRow(select))

        names = ", ".join(f"<#{c}>" for c in s.channels)[:1200] or "None selected"
        if page == "main":
            row("DESTINATION", names, "Choose", "channels")
            row("CONFIGURE RULES", "Content, bot media and deletion age.", "Configure", "configure")
            row("SAVED RULES", "Edit or remove rules in selected channels.", "Manage", "rules")
            row("SERVER RULES", "Browse every saved rule in this server.", "View all", "all")
            row("TURN ON / OFF", "Resume or pause existing rules.", "Turn on / off", "toggle")
            row("CLEAN UP NOW", "Review a one-time cleanup before deleting.", "Review", "clean")
            row("ACTIVITY LOGS", "Optional logs for enabled AutoDelete channels.", "Manage", "logs")
        elif page == "channels":
            channels = discord.ui.ChannelSelect(
                channel_types=[discord.ChannelType.text],
                min_values=1,
                max_values=25,
                placeholder="Add destination channels",
            )

            async def choose(values, i):
                member = s.guild.get_member(s.owner)
                for v in values:
                    c = s.guild.get_channel(v.id)
                    if not c or not member or not c.permissions_for(member).view_channel:
                        raise ValueError("Choose accessible channels in this server.")
                s.channels = list(dict.fromkeys(s.channels + [v.id for v in values]))
                await s.show(i, "channels")

            picker(channels, choose)
            categories = discord.ui.ChannelSelect(
                channel_types=[discord.ChannelType.category],
                min_values=1,
                max_values=25,
                placeholder="Add channels from categories",
            )

            async def groups(values, i):
                ids = {v.id for v in values}
                member = s.guild.get_member(s.owner)
                s.channels = list(
                    dict.fromkeys(
                        s.channels
                        + [
                            c.id
                            for c in s.guild.text_channels
                            if c.category_id in ids
                            and member
                            and c.permissions_for(member).view_channel
                        ]
                    )
                )
                await s.show(i, "channels")

            picker(categories, groups)

            async def clear(i):
                s.channels = []
                await s.show(i, "channels")

            row(
                "CLEAR DESTINATIONS",
                "Remove selected destinations from this draft.",
                "Clear",
                action=clear,
            )
        elif page in ("configure", "clean"):
            row("DESTINATION", names, "Choose", "channels")
            row(
                "CONTENT & BOT MEDIA",
                ", ".join(LABELS[k] for k in s.content)[:1000] or "Not selected",
                "Choose",
                "content",
            )
            row("TIMEFRAME", f"{s.seconds // 60:,} minutes", "Edit", "time")
            if s.clean:
                windows = discord.ui.Select(
                    options=[
                        discord.SelectOption(
                            label="Within the past timeframe",
                            value="within",
                            default=s.window == "within",
                        ),
                        discord.SelectOption(
                            label="Older than the timeframe",
                            value="older",
                            default=s.window == "older",
                        ),
                    ]
                )

                async def window(values, i):
                    s.window = values[0]
                    await s.show(i, "clean")

                picker(windows, window)
                scans = discord.ui.Select(
                    placeholder="Maximum posts to scan per channel",
                    options=[
                        discord.SelectOption(
                            label=str(n) if n else "All in time window",
                            value=str(n),
                            default=s.scan == n,
                        )
                        for n in (100, 250, 500, 1000, 0)
                    ],
                )

                async def scan(values, i):
                    s.scan = int(values[0])
                    await s.show(i, "clean")

                picker(scans, scan)
            row(
                "PREVIEW & SAVE" if not s.clean else "REVIEW CLEANUP",
                "Nothing changes before confirmation.",
                "Review",
                "confirm_clean" if s.clean else "review",
            )
        elif page == "content":
            select = discord.ui.Select(
                placeholder="Content choices and bot media overrides",
                min_values=1,
                max_values=len(LABELS),
                options=[
                    discord.SelectOption(label=v, value=k, default=k in s.content)
                    for k, v in LABELS.items()
                ],
            )

            async def content(values, i):
                s.content = normalize(values)
                await s.show(i, "content")

            picker(select, content)
            panel.add_item(
                discord.ui.TextDisplay(
                    "-# Keep bot media protects the entire matching post from this rule. "
                    "Other enabled rules can still apply."
                )
            )
        elif page == "time":
            select = discord.ui.Select(
                options=[
                    discord.SelectOption(
                        label=label, value=str(seconds), default=s.seconds == seconds
                    )
                    for label, seconds in PRESETS.items()
                ]
            )

            async def duration(values, i):
                s.seconds = int(values[0])
                await s.show(i, "time")

            picker(select, duration)
            row(
                "CUSTOM TIMEFRAME",
                "One minute to one year; months use 30 days.",
                "Edit",
                modal=False,
            )
        elif page in ("review", "confirm_clean"):
            panel.add_item(
                discord.ui.TextDisplay(
                    f"{names}\n{safe(', '.join(LABELS[k] for k in s.content))}\n"
                    f"Age: {s.seconds // 60:,} minutes"
                )
            )

            async def confirm(i):
                keys = normalize(s.content)
                channels = s.destinations(keys)
                if page == "confirm_clean":
                    if s.scan == 0 and s.window != "within":
                        raise ValueError("All messages requires Within the past timeframe.")
                    results = []
                    for channel in channels:
                        scanned, deleted, threads = await s.cog.service.cleanup(
                            channel, keys, s.seconds, s.scan, i.user, age_mode=s.window
                        )
                        results.append(
                            f"<#{channel.id}>: {scanned} scanned, "
                            f"{deleted} messages and {threads} threads deleted."
                        )
                    await s.show(i)
                    await i.followup.send("\n".join(results)[:1900], ephemeral=True)
                else:
                    if s.original:
                        if set(s.channels) != {s.original["channel"]}:
                            raise ValueError("Keep the original channel when editing a saved rule.")
                        s.cog.store.update_rule(
                            s.guild.id,
                            s.original,
                            keys,
                            s.seconds,
                            f"{s.seconds // 60} minutes",
                            i.user.id,
                        )
                    else:
                        s.cog.store.save(
                            s.guild.id,
                            s.channels,
                            keys,
                            s.seconds,
                            f"{s.seconds // 60} minutes",
                            i.user.id,
                        )
                    s.original = None
                    await s.show(i)
                    await i.followup.send(
                        "Rules saved. Existing paused rules remain paused.", ephemeral=True
                    )

            row(
                "CONFIRM CLEANUP" if s.clean else "SAVE RULES",
                "This cleanup deletes matching posts now."
                if s.clean
                else "Automatic deletion starts at the saved rule's start boundary.",
                "Delete now" if s.clean else "Save",
                action=confirm,
            )
        elif page in ("rules", "all"):
            records = [
                r
                for r in s.cog.store.list(s.guild.id)
                if page == "all" or r["channel"] in s.channels
            ]
            s.page = min(s.page, max(0, (len(records) - 1) // 3))
            for rule in records[s.page * 3 : (s.page + 1) * 3]:

                async def edit(i, rule=rule):
                    s.channels, s.content, s.seconds = (
                        [rule["channel"]],
                        list(rule["content"]),
                        rule["seconds"],
                    )
                    s.original, s.clean = rule, False
                    await s.show(i, "configure")

                row(
                    f"RULE {rule['id']}",
                    f"<#{rule['channel']}> · "
                    f"{'On' if rule['enabled'] else 'Off'} · {safe(rule['timeframe'])}",
                    "Edit",
                    action=edit,
                    disabled=s.guild.get_channel(rule["channel"]) is None,
                )

                async def remove(i, rule=rule):
                    s.original = rule
                    await s.show(i, "remove")

                row("REMOVE RULE", "Existing posts and threads remain.", "Review", action=remove)
            if not records:
                panel.add_item(discord.ui.TextDisplay("No saved rules."))
            for delta, label in ((-1, "Previous"), (1, "Next")):

                async def turn(i, delta=delta):
                    s.page += delta
                    await s.show(i, page)

                row(
                    label.upper(),
                    "Rule pages.",
                    label,
                    action=turn,
                    disabled=s.page == 0 if delta < 0 else (s.page + 1) * 3 >= len(records),
                )

            async def new(i):
                s.original, s.content, s.clean = None, [], False
                await s.show(i, "configure")

            row("ADD RULE", "Add a separate rule for selected channels.", "Configure", action=new)
        elif page == "remove":

            async def remove(i):
                if not s.original:
                    raise ValueError("Choose a saved rule first.")
                s.cog.store.delete(s.guild.id, s.original["id"], s.original)
                s.original = None
                await s.show(i, "rules")

            row(
                "REMOVE RULE",
                "Stops automatic deletion; existing posts remain.",
                "Confirm removal",
                action=remove,
            )
        elif page == "toggle":
            for enabled, label in ((True, "Turn on"), (False, "Turn off")):

                async def toggle(i, enabled=enabled):
                    if not s.channels:
                        raise ValueError("Choose destination channels first.")
                    if enabled:
                        for rule in s.cog.store.list(s.guild.id):
                            if rule["channel"] in s.channels:
                                s.destinations(rule["content"])
                    count = s.cog.store.toggle(s.guild.id, s.channels, enabled)
                    await s.show(i)
                    await i.followup.send(f"{count} rules updated.", ephemeral=True)

                row(label.upper(), "Preserves saved content and timeframes.", label, action=toggle)
        elif page == "logs":
            panel.add_item(
                discord.ui.TextDisplay(
                    f"Sources: {names}\n-# Only enabled AutoDelete sources can be configured. "
                    "Images and videos are never retained. Log messages expire after 15 days."
                )
            )
            select = discord.ui.ChannelSelect(
                channel_types=[discord.ChannelType.text],
                min_values=1,
                max_values=1,
                placeholder="Activity log destination",
            )

            async def destination(values, i):
                s.log_destination = values[0].id
                await s.show(i, "logs")

            picker(select, destination)
            select = discord.ui.Select(
                min_values=1,
                max_values=len(TYPES),
                options=[
                    discord.SelectOption(label=v, value=k, default=k in s.log_types)
                    for k, v in TYPES.items()
                ],
            )

            async def types(values, i):
                s.log_types = list(values)
                await s.show(i, "logs")

            picker(select, types)
            row("LOG TITLE", safe(s.log_title), "Edit", modal=True)
            row(
                "REVIEW LOGGING",
                "Confirm sources, destination and activity types.",
                "Review",
                "log_review",
            )

            async def disable(i):
                for cid in s.channels:
                    with s.cog.store.db:
                        s.cog.store.db.execute(
                            "UPDATE activity_log_config SET enabled=0 WHERE guild=? AND source=?",
                            (s.guild.id, cid),
                        )
                await s.show(i, "logs")

            row(
                "TURN OFF LOGGING",
                "Disable logging for selected sources.",
                "Turn off",
                action=disable,
            )
        elif page == "log_review":
            panel.add_item(
                discord.ui.TextDisplay(
                    f"Sources: {names}\nDestination: <#{s.log_destination}>\n"
                    f"Title: {safe(s.log_title)}\n{', '.join(TYPES[k] for k in s.log_types)}"
                )
            )

            async def save_logs(i):
                channel = s.guild.get_channel(s.log_destination)
                member = s.guild.get_member(s.owner)
                if not isinstance(channel, discord.TextChannel) or not member:
                    raise ValueError("Choose a log destination.")
                if not channel.permissions_for(member).view_channel:
                    raise ValueError("Choose an accessible log destination.")
                p = channel.permissions_for(s.guild.me)
                if not (
                    p.view_channel and p.send_messages and p.embed_links and p.read_message_history
                ):
                    raise ValueError(
                        "The bot needs View Channel, Send Messages, Embed Links "
                        "and Read Message History in the log destination."
                    )
                active = s.cog.service.logs.active_channels(s.guild.id)
                if not s.channels or any(
                    cid not in active or cid == channel.id for cid in s.channels
                ):
                    raise ValueError(
                        "Choose enabled AutoDelete sources, separate from the log channel."
                    )
                for cid in s.channels:
                    s.cog.service.logs.save(
                        s.guild.id, cid, channel.id, s.log_types, title=s.log_title, color=0xBDC3CA
                    )
                await s.show(i, "logs")

            row(
                "SAVE ACTIVITY LOGS",
                "Applies to new activity; private media is omitted.",
                "Save",
                action=save_logs,
            )
        if main:
            panel.add_item(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem("attachment://autodelete_footer.png")
                )
            )

        async def back(i):
            if page == "main" and s.command_center is not None:
                s.command_center.section = "general"
                await s.command_center.refresh(i)
                s.generation += 1
                return
            await s.show(
                i,
                "main"
                if main or page in ("channels", "toggle", "logs")
                else "logs"
                if page == "log_review"
                else "clean"
                if s.clean
                else "configure",
            )

        async def cancel(i):
            from ky_bot.checklists.inline import InlineView

            s.generation += 1
            await i.edit_original_response(
                content=None,
                attachments=[],
                view=InlineView().add_item(
                    discord.ui.TextDisplay(
                        "Command Center closed. Use /settings to reopen."
                        if s.command_center is not None
                        else "AutoDelete menu closed."
                    )
                ),
            )

        navigation = []
        actions = (
            (("Close", cancel),)
            if page == "main" and s.command_center is None
            else (("Back", back), ("Close", cancel))
        )
        for label, action in actions:
            button = discord.ui.Button(label=label)

            async def navigate(i, action=action):
                async with s.lock:
                    if not await s.allowed(i, self.generation):
                        return
                    await i.response.defer()
                    await action(i)

            button.callback = navigate
            navigation.append(button)
        panel.add_item(discord.ui.ActionRow(*navigation))
        self.add_item(panel)

    async def interaction_check(self, i):
        return await self.session.allowed(i, self.generation)

    async def on_error(self, i, error, item):
        await handle_error(i, error)


class AutoDelete(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if bot.settings.database_url:
            from .postgres import PostgresStore

            self.store = PostgresStore(bot.settings.database_url)
        else:
            path = bot.settings.database_path
            self.store = RuleStore(
                ":memory:" if str(path) == ":memory:" else path.with_name("ky-autodelete.sqlite3")
            )
        self.service = AutoDeleteService(bot, self.store)

    async def cog_load(self):
        self.service.start()

    async def cog_unload(self):
        await self.service.close()
        self.store.db.close()

    @commands.Cog.listener()
    async def on_message(self, message):
        await self.service.logs.on_message(message)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        await self.service.logs.on_edit(payload)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload):
        await self.service.logs.on_delete(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        await self.service.logs.on_reaction(payload, "reaction_remove")

    @commands.Cog.listener()
    async def on_raw_reaction_clear(self, payload):
        await self.service.logs.on_reaction(payload, "reaction_clear")

    @commands.Cog.listener()
    async def on_raw_bulk_message_delete(self, payload):
        await self.service.logs.on_delete(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_clear_emoji(self, payload):
        await self.service.logs.on_reaction(payload, "reaction_clear")

    @app_commands.command(name="autodelete", description="Configure channel auto-delete rules.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def autodelete(self, interaction):
        await self.open_menu(interaction)

    async def open_menu(self, interaction, *, command_center=None):
        await interaction.response.defer(ephemeral=True, thinking=command_center is None)
        session = Session(self, interaction)
        session.command_center = command_center
        await session.show(interaction)


async def setup(bot):
    await bot.add_cog(AutoDelete(bot))
