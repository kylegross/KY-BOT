"""Private, inline AutoReact configuration; opening a menu creates no rules."""

import asyncio
import copy
import json
from importlib.resources import files

import discord
from discord import app_commands
from discord.ext import commands

from ky_bot.admin_functions.autothread.command import artwork as thread_artwork
from ky_bot.checklists.board import safe
from ky_bot.checklists.inline import InlineView
from ky_bot.errors import report_error
from ky_bot.views.private_style import private_heading

from .service import AutoReactService, emoji_key, parse_emojis, permissions, resolve_emoji
from .store import CONTENT, FILTERS, Store, defaults, validate

CATALOG = json.loads(files(__package__).joinpath("emoji_catalog.json").read_text(encoding="utf-8"))[
    "emojis"
]


def artwork(design=None):
    if design is not None:
        from ky_bot.design.menu_design import menu_files

        return menu_files(
            design,
            "AUTOREACT",
            "CHANNEL AUTOMATION",
            ("ky_header_autoreact.png", "dark_silver_icon.png", "ky_autothread_title.png"),
        )
    uploads = thread_artwork()
    uploads[0].close()
    uploads[0] = discord.File(
        files("ky_bot.design").joinpath("assets/header/ky_header_autoreact.png").open("rb"),
        filename="ky_header_autoreact.png",
    )
    return uploads


class Session:
    def __init__(self, cog, interaction):
        self.cog, self.guild, self.owner = cog, interaction.guild, interaction.user.id
        self.channels = (
            [interaction.channel_id] if isinstance(interaction.channel, discord.TextChannel) else []
        )
        self.draft, self.expected = defaults(), {}
        self.generation, self.page, self.query, self.group = 0, 0, "", "All"
        self.emoji_index = 0
        self.lock = asyncio.Lock()
        self.design = None

    async def allowed(self, interaction, generation):
        p = interaction.user.guild_permissions
        if (
            interaction.guild_id == self.guild.id
            and interaction.user.id == self.owner
            and (p.manage_guild or p.administrator)
            and generation == self.generation
        ):
            return True
        await interaction.response.send_message(
            "Reopen /autoreact with server management permissions.", ephemeral=True
        )
        return False

    def destinations(self, enabling=True):
        member = self.guild.get_member(self.owner)
        if not self.channels:
            raise ValueError("Choose at least one destination.")
        for cid in self.channels:
            channel = self.guild.get_channel(cid)
            if (
                not isinstance(channel, discord.TextChannel)
                or not member
                or not channel.permissions_for(member).view_channel
            ):
                raise ValueError("Choose accessible text channels in this server.")
            if enabling:
                permissions(channel)
        return self.channels

    def load(self):
        rules = [self.cog.store.get(self.guild.id, cid) for cid in self.channels]
        settings = [r["settings"] for r in rules if r]
        if settings and any(s != settings[0] for s in settings):
            raise ValueError(
                "These channels have different settings. Select one saved rule to edit, "
                "or clear destinations to start a new setup."
            )
        self.draft = copy.deepcopy(settings[0]) if settings else defaults()
        self.expected = {cid: r["revision"] if r else None for cid, r in zip(self.channels, rules)}

    async def show(self, interaction, page="main"):
        _, profiles = await self.cog.bot.server_settings.repository.get_designs(self.guild.id)
        from ky_bot.design.menu_design import profile_for

        self.design = profile_for(profiles, "autoreact") if "autoreact" in profiles else None
        self.generation += 1
        view = Menu(self, page)
        uploads = (
            await asyncio.to_thread(artwork, self.design)
            if page in ("main", "configure", "rules", "all")
            else []
        )
        try:
            await interaction.edit_original_response(
                content=None, embeds=[], attachments=uploads, view=view
            )
        finally:
            for upload in uploads:
                upload.close()


class EmojiModal(discord.ui.Modal):
    def __init__(self, session, search=False):
        super().__init__(title="Search emojis" if search else "Paste emojis")
        self.session, self.generation, self.search = session, session.generation, search
        self.text = discord.ui.TextInput(
            label="Emoji name" if search else "Emojis or :server_emoji_name:", max_length=2000
        )
        self.add_item(self.text)

    async def on_submit(self, interaction):
        s = self.session
        async with s.lock:
            if not await s.allowed(interaction, self.generation):
                return
            if self.search:
                s.query, s.page = str(self.text).strip(), 0
            else:
                values = list(s.draft["emojis"])
                keys = {emoji_key(e) for e in values}
                for value in parse_emojis(str(self.text), s.guild):
                    if emoji_key(value) not in keys:
                        values.append(value)
                        keys.add(emoji_key(value))
                if len(values) > 20:
                    raise ValueError("Choose no more than 20 emojis.")
                s.draft["emojis"] = values
            await interaction.response.defer()
            await s.show(interaction, "browse" if self.search else "emojis")

    async def on_error(self, interaction, error):
        if isinstance(error, ValueError):
            await interaction.response.send_message(str(error), ephemeral=True)
        else:
            await report_error(interaction, error)


class Menu(discord.ui.LayoutView):
    def __init__(self, s, page="main"):
        super().__init__(timeout=600)
        self.session, self.generation = s, s.generation
        from ky_bot.design.collection import COLOURS

        colour = int(COLOURS[s.design["style"]][1:], 16) if s.design else 0xBDC3CA
        panel = discord.ui.Container(accent_colour=colour)
        main = page in ("main", "configure", "rules", "all")
        if main:
            for name in ("ky_header_autoreact.png", "ky_autothread_title.png"):
                panel.add_item(
                    discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://" + name))
                )
        else:
            panel.add_item(
                discord.ui.TextDisplay(
                    private_heading(
                        {
                            "channels": "DESTINATION",
                            "emojis": "EMOJIS",
                            "browse": "ADD EMOJI",
                            "server": "SERVER EMOJIS",
                            "content": "CONTENT & TRIGGERS",
                            "filters": "FILTERS",
                            "review": "PREVIEW & SAVE",
                            "remove": "REMOVE RULE",
                            "toggle": "TURN ON / OFF",
                        }.get(page, "AUTOREACT")
                    )
                )
            )

        def row(
            title,
            description,
            label,
            target=None,
            action=None,
            *,
            disabled=False,
            style=discord.ButtonStyle.secondary,
        ):
            b = discord.ui.Button(label=label, disabled=disabled, style=style)

            async def click(i):
                async with s.lock:
                    await i.response.defer()
                    if action:
                        await action(i)
                    else:
                        if target == "configure":
                            s.destinations(False)
                            s.load()
                        s.page = 0
                        await s.show(i, target)

            b.callback = click
            panel.add_item(discord.ui.Section(f"**{title}**\n-# {description}", accessory=b))

        def picker(select, action):
            async def choose(i):
                async with s.lock:
                    await i.response.defer()
                    await action(select.values, i)

            select.callback = choose
            panel.add_item(discord.ui.ActionRow(select))

        destinations = ", ".join(f"<#{cid}>" for cid in s.channels) or "None selected"
        if page == "main":
            row("DESTINATION", destinations, "Choose", "channels")
            row(
                "CONFIGURE REACTIONS",
                "Choose emojis, triggers and filters.",
                "Configure",
                "configure",
            )
            row("SAVED RULES", "Manage rules for selected channels.", "Manage", "rules")
            row("SERVER RULES", "View all reaction rules in this server.", "View all", "all")
            row("REMOVE RULE", "Existing reactions remain.", "Review", "remove")
            row("TURN ON / OFF", "Resume or pause saved rules.", "Turn on / off", "toggle")
        elif page == "channels":
            select = discord.ui.ChannelSelect(
                channel_types=[discord.ChannelType.text],
                min_values=1,
                max_values=25,
                placeholder="Choose destination channels",
            )

            async def selected(values, i):
                s.channels = list(dict.fromkeys(s.channels + [v.id for v in values]))
                s.expected, s.draft = {}, defaults()
                await s.show(i)

            picker(select, selected)
            categories = discord.ui.ChannelSelect(
                channel_types=[discord.ChannelType.category],
                min_values=1,
                max_values=25,
                placeholder="Add channels from categories",
            )

            async def selected_categories(values, i):
                member = s.guild.get_member(s.owner)
                ids = {v.id for v in values}
                channels = [
                    c.id
                    for c in s.guild.text_channels
                    if c.category_id in ids and member and c.permissions_for(member).view_channel
                ]
                s.channels = list(dict.fromkeys(s.channels + channels))
                s.expected, s.draft = {}, defaults()
                await s.show(i)

            picker(categories, selected_categories)

            async def clear(i):
                s.channels, s.expected, s.draft = [], {}, defaults()
                await s.show(i, "channels")

            row("CLEAR DESTINATIONS", "Start a fresh selection.", "Clear", action=clear)
        elif page == "configure":
            row("DESTINATION", destinations, "Choose", "channels")
            row("EMOJIS", " ".join(s.draft["emojis"]) or "None added", "Edit", "emojis")
            row(
                "CONTENT & TRIGGERS",
                ", ".join(CONTENT[k] for k in s.draft["content"]),
                "Choose",
                "content",
            )
            row("FILTERS", "Bots, webhooks and thread replies.", "Edit", "filters")
            row("PREVIEW & SAVE", "Apply to selected channels; new posts only.", "Review", "review")
        elif page == "emojis":
            values = s.draft["emojis"]
            s.emoji_index = min(s.emoji_index, max(0, len(values) - 1))
            if values:
                select = discord.ui.Select(
                    placeholder="Manage an emoji",
                    options=[
                        discord.SelectOption(
                            label=f"{n + 1}. {e}"[:100], value=str(n), default=n == s.emoji_index
                        )
                        for n, e in enumerate(values)
                    ],
                )

                async def selected(values, i):
                    s.emoji_index = int(values[0])
                    await s.show(i, "emojis")

                picker(select, selected)
            row(
                "ADD EMOJI",
                "Browse standard emojis or use server emojis.",
                "Add emoji",
                "browse",
                disabled=len(values) >= 20,
            )
            for delta, label in ((-1, "Move up"), (1, "Move down"), (0, "Remove")):

                async def move(i, delta=delta):
                    index = s.emoji_index
                    if delta:
                        values[index], values[index + delta] = values[index + delta], values[index]
                        s.emoji_index += delta
                    else:
                        values.pop(index)
                    await s.show(i, "emojis")

                row(
                    label.upper(),
                    "Reaction order follows this list.",
                    label,
                    action=move,
                    disabled=not values
                    or (delta == -1 and s.emoji_index == 0)
                    or (delta == 1 and s.emoji_index == len(values) - 1),
                )
        elif page in ("browse", "server"):
            keys = {emoji_key(e) for e in s.draft["emojis"]}
            records = (
                [
                    {"emoji": str(e), "name": e.name, "group": "Server"}
                    for e in s.guild.emojis
                    if e.is_usable()
                ]
                if page == "server"
                else CATALOG
            )
            records = [
                r
                for r in records
                if emoji_key(r["emoji"]) not in keys
                and (s.group == "All" or page == "server" or r["group"] == s.group)
                and s.query.lower() in r["name"].lower()
            ]
            s.page = min(s.page, max(0, (len(records) - 1) // 25))
            if records:
                batch = records[s.page * 25 : (s.page + 1) * 25]
                select = discord.ui.Select(
                    placeholder=f"Choose emoji · Page {s.page + 1}",
                    options=[
                        discord.SelectOption(
                            label=r["name"][:100],
                            value=str(n),
                            emoji=discord.PartialEmoji.from_str(r["emoji"]),
                        )
                        for n, r in enumerate(batch)
                    ],
                )

                async def add(values, i):
                    if len(s.draft["emojis"]) >= 20:
                        raise ValueError("Choose no more than 20 emojis.")
                    s.draft["emojis"].append(batch[int(values[0])]["emoji"])
                    await s.show(i, "emojis")

                picker(select, add)
            else:
                panel.add_item(discord.ui.TextDisplay("No matching usable emojis."))
            if page == "browse":
                groups = ["All"] + sorted({r["group"] for r in CATALOG})
                select = discord.ui.Select(
                    placeholder="Emoji category",
                    options=[
                        discord.SelectOption(label=g, value=g, default=g == s.group) for g in groups
                    ],
                )

                async def group(values, i):
                    s.group, s.page = values[0], 0
                    await s.show(i, "browse")

                picker(select, group)
                row("SERVER EMOJIS", "Includes usable animated emojis.", "Browse", "server")
            for delta, label in ((-1, "Previous"), (1, "Next")):

                async def turn(i, delta=delta):
                    s.page += delta
                    await s.show(i, page)

                row(
                    label.upper(),
                    "Browse emoji pages.",
                    label,
                    action=turn,
                    disabled=s.page == 0 if delta < 0 else (s.page + 1) * 25 >= len(records),
                )
            for search, label in ((True, "Search"), (False, "Paste emojis")):
                button = discord.ui.Button(label=label)

                async def modal(i, search=search):
                    await i.response.send_modal(EmojiModal(s, search))

                button.callback = modal
                panel.add_item(discord.ui.Section(f"**{label.upper()}**", accessory=button))
        elif page == "content":
            select = discord.ui.Select(
                min_values=1,
                max_values=len(CONTENT),
                options=[
                    discord.SelectOption(label=v, value=k, default=k in s.draft["content"])
                    for k, v in CONTENT.items()
                ],
            )

            async def selected(values, i):
                s.draft["content"] = ["all"] if "all" in values else list(values)
                await s.show(i, "content")

            picker(select, selected)
        elif page == "filters":
            for key, label in FILTERS.items():

                async def toggle(i, key=key):
                    s.draft[key] = not s.draft[key]
                    await s.show(i, "filters")

                row(label.upper(), "On" if s.draft[key] else "Off", "Toggle", action=toggle)
        elif page == "review":
            panel.add_item(
                discord.ui.TextDisplay(
                    f"{destinations}\n{safe(' '.join(s.draft['emojis']))}\n"
                    f"Triggers: {', '.join(CONTENT[k] for k in s.draft['content'])}"
                )
            )

            async def save(i):
                s.destinations()
                validate(s.draft)
                for e in s.draft["emojis"]:
                    resolve_emoji(e, s.guild)
                s.cog.store.save(s.guild.id, s.channels, s.draft, s.expected)
                await s.show(i)
                await i.followup.send(
                    "Reaction rules saved. Applies to new posts; paused rules stay paused.",
                    ephemeral=True,
                )

            row(
                "SAVE RULES",
                "Existing paused states are retained.",
                "Save",
                action=save,
                style=discord.ButtonStyle.success,
            )
        elif page in ("rules", "all"):
            records = [
                r
                for r in s.cog.store.list(s.guild.id)
                if page == "all" or r["channel"] in s.channels
            ]
            s.page = min(s.page, max(0, (len(records) - 1) // 4))
            for rule in records[s.page * 4 : (s.page + 1) * 4]:

                async def edit(i, rule=rule):
                    s.channels = [rule["channel"]]
                    s.destinations(False)
                    s.load()
                    await s.show(i, "configure")

                row(
                    f"CHANNEL {rule['channel']}",
                    f"<#{rule['channel']}> · {'On' if rule['enabled'] else 'Off'} · "
                    f"{' '.join(rule['settings']['emojis'])}\n{safe(rule['error'])}",
                    "Edit",
                    action=edit,
                    disabled=s.guild.get_channel(rule["channel"]) is None,
                )

                async def remove(i, rule=rule):
                    s.channels = [rule["channel"]]
                    await s.show(i, "remove")

                row("REMOVE RULE", "Existing reactions remain.", "Review", action=remove)
            if not records:
                panel.add_item(discord.ui.TextDisplay("No saved reaction rules."))
            for delta, label in ((-1, "Previous"), (1, "Next")):

                async def turn(i, delta=delta):
                    s.page += delta
                    await s.show(i, page)

                row(
                    label.upper(),
                    "Browse saved rules.",
                    label,
                    action=turn,
                    disabled=s.page == 0 if delta < 0 else (s.page + 1) * 4 >= len(records),
                )
        elif page in ("toggle", "remove"):
            rules = [s.cog.store.get(s.guild.id, cid) for cid in s.channels]
            expected = {r["channel"]: r["revision"] for r in rules if r}
            panel.add_item(discord.ui.TextDisplay(destinations))
            for action, label in (
                [("remove", "Confirm removal")]
                if page == "remove"
                else [("on", "Turn on"), ("off", "Turn off")]
            ):

                async def apply(i, action=action):
                    if action == "on":
                        s.destinations()
                    if not expected:
                        raise ValueError("Select channels with saved rules first.")
                    s.cog.store.action(s.guild.id, expected, action)
                    await s.show(i)

                row(
                    label.upper(),
                    "Existing reactions remain.",
                    label,
                    action=apply,
                    disabled=not expected,
                )
        if main:
            panel.add_item(
                discord.ui.MediaGallery(
                    discord.MediaGalleryItem("attachment://dark_silver_icon.png")
                )
            )
        back = discord.ui.Button(label="Back")

        async def go_back(i):
            await i.response.defer()
            await s.show(
                i,
                "main"
                if main or page in ("channels", "toggle", "remove")
                else "emojis"
                if page in ("browse", "server")
                else "configure",
            )

        back.callback = go_back
        close = discord.ui.Button(label="Close" if page == "main" else "Cancel setup")

        async def cancel(i):
            await i.response.defer()
            s.draft, s.expected = defaults(), {}
            if page == "main":
                s.generation += 1
                await i.edit_original_response(
                    content=None,
                    attachments=[],
                    view=InlineView().add_item(discord.ui.TextDisplay("AutoReact menu closed.")),
                )
            else:
                await s.show(i)

        close.callback = cancel
        panel.add_item(discord.ui.ActionRow(back, close))
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


class AutoReact(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        if bot.settings.database_url:
            from .postgres import PostgresStore

            self.store = PostgresStore(bot.settings.database_url)
        else:
            path = bot.settings.database_path
            self.store = Store(
                ":memory:" if str(path) == ":memory:" else path.with_name("ky-autoreact.sqlite3")
            )
        self.service = AutoReactService(bot, self.store)

    async def cog_load(self):
        self.service.start()

    async def cog_unload(self):
        await self.service.close()

    @commands.Cog.listener()
    async def on_message(self, message):
        await self.service.on_message(message)

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload):
        await self.service.on_raw_message_edit(payload)

    @app_commands.command(
        name="autoreact", description="Configure automatic emoji reactions for server channels."
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def autoreact(self, interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        session = Session(self, interaction)
        _, profiles = await self.bot.server_settings.repository.get_designs(interaction.guild_id)
        from ky_bot.design.menu_design import profile_for

        session.design = profile_for(profiles, "autoreact") if "autoreact" in profiles else None
        uploads = await asyncio.to_thread(artwork, session.design)
        try:
            await interaction.edit_original_response(view=Menu(session), attachments=uploads)
        finally:
            for upload in uploads:
                upload.close()


async def setup(bot):
    await bot.add_cog(AutoReact(bot))
