"""Settings-only appearance drafts with disabled live-layout previews and atomic apply."""

import asyncio
import base64
import copy
import io

import discord

from ky_bot.checklists.board import BoardView, safe
from ky_bot.design.menu_design import (
    AUTOMATION_KEYS,
    FINISHES,
    automation_profile,
    board_with_profile,
    default_profile,
    normalized_upload,
    profile_for,
)
from ky_bot.errors import report_error
from ky_bot.views.private_style import private_heading

TARGETS = {
    "settings:overview": "Server settings",
    "settings:general": "General settings",
    "settings:welcome": "Welcome settings",
    "settings:logging": "Server activity",
    "settings:autothread": "AutoThread",
    "settings:autoreact": "AutoReact",
    "settings:autodelete": "AutoDelete",
    "checklist": "All checklists / default",
    "welcome_image": "Welcome image",
}


class DesignSession:
    def __init__(self, interaction, service, revision, profiles):
        self.owner, self.guild_id = interaction.user.id, interaction.guild_id
        self.service, self.bot, self.guild = service, interaction.client, interaction.guild
        self.revision, self.profiles = revision, profiles
        self.targets = dict(TARGETS)
        checklists = getattr(self.bot, "checklists", None)
        if checklists:
            for board in checklists.store.boards():
                if board["guild"] == self.guild_id:
                    channel = self.guild.get_channel(board["channel"])
                    name = getattr(channel, "name", board["channel"])
                    self.targets[f"checklist:{board['channel']}"] = (
                        f"Checklist: {board['name'][:50]} · #{name}"
                    )
        self.selected, self.drafts = [], {}
        self.generation, self.page, self.preview_key = 0, 0, None
        self.lock = asyncio.Lock()

    @classmethod
    async def create(cls, interaction, service):
        revision, profiles = await service.repository.get_designs(interaction.guild_id)
        return cls(interaction, service, revision, profiles)

    async def allowed(self, interaction, generation):
        if (
            interaction.guild_id == self.guild_id
            and interaction.user.id == self.owner
            and interaction.permissions.administrator
            and generation == self.generation
        ):
            return True
        await interaction.response.send_message(
            "Reopen /settings as an administrator to edit designs.", ephemeral=True
        )
        return False

    async def select(self, values):
        if any(key not in self.targets for key in values):
            raise ValueError("Select menus from this server's design menu.")
        if "welcome_image" in values and len(values) > 1:
            raise ValueError(
                "Edit the single-image welcome card separately from header/footer menus."
            )
        self.selected = list(values)
        for key in values:
            if key not in self.drafts:
                profile = (
                    automation_profile(self.profiles, key.removeprefix("settings:"))
                    if key.removeprefix("settings:") in AUTOMATION_KEYS
                    else profile_for(self.profiles, key)
                )
                if key.startswith("checklist:") and key not in self.profiles:
                    board = self.bot.checklists.store.board(int(key.split(":")[1]))
                    if board and "checklist" not in self.profiles:
                        profile.update(
                            style=board["style"],
                            height=board["height"],
                            title=board["name"],
                            footer_text=board["footer_text"],
                        )
                        for field in ("header_background", "footer_background", "footer_icon"):
                            profile[field] = (
                                base64.b64encode(board[field]).decode() if board[field] else None
                            )
                if key == "welcome_image" and key not in self.profiles:
                    art = await self.service.repository.get_welcome_artwork(self.guild_id)
                    profile.update(
                        title=art.title,
                        dim=art.dim,
                        header_background=base64.b64encode(art.background).decode()
                        if art.background
                        else None,
                    )
                self.drafts[key] = profile

    def change(self, **values):
        for key in self.selected:
            self.drafts[key].update(values)

    async def show(self, interaction, page="edit", notice=None):
        self.generation += 1
        view = Editor(self, page, notice)
        await interaction.edit_original_response(
            content=None,
            embeds=[],
            attachments=[],
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def preview(self, interaction, key=None):
        key = key or self.preview_key or self.selected[0]
        self.preview_key = key
        profile = self.drafts[key]
        uploads = []
        if key.startswith("settings:") and key.removeprefix("settings:") not in AUTOMATION_KEYS:
            from ky_bot.admin_functions.settings_view import SettingsView, settings_files

            section = key.split(":")[1]
            sample = SettingsView(self.owner, self.guild_id, self.service)
            sample.settings = await self.service.repository.get(self.guild_id)
            sample.guild, sample.design = self.guild, profile
            sample.show_section(section)
            uploads = await asyncio.to_thread(settings_files, section, profile)
        elif key.removeprefix("settings:") in AUTOMATION_KEYS:
            automation = key.removeprefix("settings:")
            if automation == "autoreact":
                from ky_bot.admin_functions.autoreact.command import Menu, Session, artwork

                cog = self.bot.get_cog("AutoReact")
            elif automation == "autothread":
                from ky_bot.admin_functions.autothread.command import Menu, Session, artwork

                cog = self.bot.get_cog("AutoThread")
            else:
                from ky_bot.admin_functions.autodelete.command import Menu, Session, artwork

                cog = self.bot.get_cog("AutoDelete")
            session = Session(cog, interaction)
            session.design = profile
            sample = Menu(session)
            uploads = await asyncio.to_thread(artwork, profile)
        elif key == "welcome_image":
            image = await self.service.render_welcome_card(interaction.user, design=profile)
            sample = discord.ui.LayoutView()
            sample.add_item(
                discord.ui.MediaGallery(discord.MediaGalleryItem("attachment://welcome_draft.png"))
            )
            uploads = [discord.File(io.BytesIO(image), filename="welcome_draft.png")]
        else:
            from ky_bot.checklists.artwork import board_artwork

            service = self.bot.checklists
            boards = [b for b in service.store.boards() if b["guild"] == self.guild_id]
            board = (
                service.store.board(int(key.split(":")[1]))
                if ":" in key
                else boards[0]
                if boards
                else None
            )
            if ":" in key and (board is None or board["guild"] != self.guild_id):
                raise ValueError("That checklist no longer exists in this server.")
            if board is None:
                # Read-only example data; no board is created in the server or database.
                from types import SimpleNamespace

                board = dict(
                    channel=0, guild=self.guild_id, name="ADMIN CHECKLIST", page=0, task_view="open"
                )
                store = SimpleNamespace(tasks=lambda _: [], categories=lambda _: [])
                service = SimpleNamespace(store=store)
            else:
                # One real example task keeps preview controls within Discord's 40-item limit.
                from types import SimpleNamespace

                tasks = service.store.tasks(board["channel"])[:1]
                categories = service.store.categories(board["channel"])
                if tasks:
                    categories = [c for c in categories if c["id"] == tasks[0]["category_id"]]
                else:
                    categories = categories[:1]
                service = SimpleNamespace(
                    store=SimpleNamespace(tasks=lambda _: tasks, categories=lambda _: categories)
                )
                board = dict(board, page=0, task_view="open")
            board = board_with_profile(board, profile)
            sample = BoardView(service, board)
            artwork = await asyncio.to_thread(
                board_artwork,
                board,
                service.store.categories(board["channel"]),
                service.store.tasks(board["channel"]),
            )
            uploads = [
                discord.File(io.BytesIO(data), filename=name) for name, data in artwork.items()
            ]
        # Sample controls never retain active callbacks or clickable links.
        for child in sample.walk_children():
            if hasattr(child, "disabled"):
                child.disabled = True
            if isinstance(child, discord.ui.Button) and child.url:
                child.url = None
                child.style = discord.ButtonStyle.secondary
        self.generation += 1
        view = Editor(self, "preview", sample=sample)
        try:
            await interaction.edit_original_response(
                content=None,
                embeds=[],
                view=view,
                attachments=uploads,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            for upload in uploads:
                upload.close()
            sample.stop()

    async def apply(self, interaction):
        if not self.selected:
            raise ValueError("Select at least one menu.")
        # Render all selected drafts before writing anything; font/image errors stay in draft.
        for key in self.selected:
            from ky_bot.design.menu_design import render_menu

            if key == "welcome_image":
                await self.service.render_welcome_card(interaction.user, design=self.drafts[key])
            else:
                await asyncio.to_thread(render_menu, self.drafts[key], "PREVIEW")
        profiles = copy.deepcopy(self.profiles)
        if "checklist" in self.selected:
            profiles = {
                key: value
                for key, value in profiles.items()
                if not key.startswith("checklist:") or key in self.selected
            }
        for key in self.selected:
            profiles[key] = copy.deepcopy(self.drafts[key])
        self.revision = await self.service.repository.apply_designs(
            self.guild_id, profiles, self.revision
        )
        self.profiles = profiles
        # Only existing board messages are refreshed. No new checklist or automation rule.
        checklists = getattr(self.bot, "checklists", None)
        if checklists:
            for board in checklists.store.boards():
                if board["guild"] == self.guild_id and (
                    "checklist" in self.selected or f"checklist:{board['channel']}" in self.selected
                ):
                    style = profile_for(profiles, f"checklist:{board['channel']}")["style"]
                    checklists.store.update_board(board["channel"], style=style, dirty=1)
            checklists.wakeup.set()
        await self.show(
            interaction,
            notice=(
                "Design applied. Existing checklists refresh automatically; "
                "private menus use it when reopened or refreshed."
            ),
        )


class AppearanceModal(discord.ui.Modal):
    def __init__(self, session, upload=None):
        super().__init__(title="Upload design image" if upload else "Edit appearance")
        self.session, self.generation, self.upload_key = session, session.generation, upload
        profile = session.drafts[session.selected[0]]
        self.fields = {}
        if upload:
            self.upload = discord.ui.FileUpload(max_values=1, required=True)
            self.add_item(
                discord.ui.Label(text="Choose an image (up to 8 MB)", component=self.upload)
            )
        else:
            welcome = session.selected == ["welcome_image"]
            specs = (
                [
                    ("title", "Welcome title", profile["title"] or "", 48),
                    ("dim", "Background darkening (0–80)", str(profile["dim"]), 2),
                ]
                if welcome
                else [
                    (
                        "title",
                        "Header title (blank keeps each menu's title)",
                        profile["title"] or "",
                        48,
                    ),
                    ("height", "Header height (240–800 pixels)", str(profile["height"]), 3),
                    ("footer_text", "Footer text (blank uses icon)", profile["footer_text"], 80),
                ]
            )
            for key, label, value, limit in specs:
                field = discord.ui.TextInput(
                    label=label, default=value, required=key in ("height", "dim"), max_length=limit
                )
                self.fields[key] = field
                self.add_item(field)

    async def on_submit(self, interaction):
        s = self.session
        async with s.lock:
            if not await s.allowed(interaction, self.generation):
                return
            await interaction.response.defer()
            if self.upload_key:
                attachment = self.upload.values[0]
                if attachment.size > 8 * 1024 * 1024:
                    raise ValueError("Use an image under 8 MB.")
                encoded = await asyncio.to_thread(
                    normalized_upload,
                    await attachment.read(),
                    icon=self.upload_key == "footer_icon",
                )
                s.change(**{self.upload_key: encoded})
                if self.upload_key == "footer_icon":
                    s.change(footer_text="")
            else:
                values = {key: str(field).strip() for key, field in self.fields.items()}
                for key, low, high in (("height", 240, 800), ("dim", 0, 80)):
                    if key in values:
                        try:
                            values[key] = int(values[key])
                        except ValueError:
                            raise ValueError(f"Enter a number from {low} to {high}.") from None
                        if not low <= values[key] <= high:
                            raise ValueError(f"Enter a number from {low} to {high}.")
                if any(any(ord(c) < 32 for c in v) for v in values.values() if isinstance(v, str)):
                    raise ValueError("Use single-line text.")
                values["title"] = values.get("title") or None
                s.change(**values)
            await s.show(interaction)

    async def on_error(self, interaction, error):
        await editor_error(interaction, error)


async def editor_error(interaction, error):
    if isinstance(error, ValueError):
        if interaction.response.is_done():
            await interaction.followup.send(str(error), ephemeral=True)
        else:
            await interaction.response.send_message(str(error), ephemeral=True)
    else:
        await report_error(interaction, error)


class Editor(discord.ui.LayoutView):
    def __init__(self, session, page="edit", notice=None, sample=None):
        super().__init__(timeout=600)
        self.session, self.generation = session, session.generation
        s = session
        if sample:
            # Preview approval controls replace the sample's bottom navigation.
            for container in sample.children:
                if isinstance(container, discord.ui.Container):
                    for child in list(container.children):
                        # Drafts use this editor's approval controls and timeout,
                        # rather than the live menu's autosave/session notice.
                        if isinstance(child, discord.ui.TextDisplay) and child.content.startswith(
                            "-# Private admin controls"
                        ):
                            container.remove_item(child)
                            continue
                        if isinstance(child, discord.ui.ActionRow) and all(
                            isinstance(button, discord.ui.Button)
                            and button.label in ("Back", "Refresh", "Close", "Preview")
                            for button in child.children
                        ):
                            container.remove_item(child)
                            continue
                        if (
                            isinstance(child, discord.ui.Section)
                            and isinstance(child.accessory, discord.ui.Button)
                            and child.accessory.label in ("Back", "Close")
                        ):
                            container.remove_item(child)
            for item in list(sample.children):
                sample.remove_item(item)
                self.add_item(item)
        panel = discord.ui.Container(accent_colour=0xBDC3CA)
        panel.add_item(
            discord.ui.TextDisplay(
                private_heading("DESIGN PREVIEW" if page == "preview" else "EMBED DESIGN")
            )
        )
        if notice:
            panel.add_item(discord.ui.TextDisplay(notice))

        def row(title, description, label, action, *, disabled=False):
            button = discord.ui.Button(label=label, disabled=disabled)

            async def click(i):
                async with s.lock:
                    if not await s.allowed(i, self.generation):
                        return
                    if isinstance(action, tuple):
                        await i.response.send_modal(AppearanceModal(s, action[0]))
                    else:
                        await i.response.defer()
                        await action(i)

            button.callback = click
            panel.add_item(discord.ui.Section(f"**{title}**\n-# {description}", accessory=button))

        async def edit(i):
            await s.show(i)

        if page == "preview":
            group = s.selected.index(s.preview_key) // 24
            preview_keys = s.selected[group * 24 : (group + 1) * 24]
            selector = discord.ui.Select(
                placeholder="Preview another selected menu",
                options=[
                    discord.SelectOption(
                        label=s.targets[key][:100], value=key, default=key == s.preview_key
                    )
                    for key in preview_keys
                ],
            )
            if len(s.selected) > 24:
                selector.append_option(
                    discord.SelectOption(label="Next preview group", value="__next")
                )

            async def change_preview(i):
                async with s.lock:
                    if not await s.allowed(i, self.generation):
                        return
                    await i.response.defer()
                    key = selector.values[0]
                    if key == "__next":
                        key = s.selected[((group + 1) * 24) % len(s.selected)]
                    await s.preview(i, key)

            selector.callback = change_preview
            panel.add_item(discord.ui.ActionRow(selector))
            row(
                "APPLY DESIGN", "Save all selected drafts for this server.", "Apply design", s.apply
            )
            row("BACK TO EDITING", "Keep the draft without applying it.", "Back to editing", edit)
        elif page in ("targets", "finish"):
            if page == "targets":
                entries = list(s.targets.items())
                s.page = min(s.page, max(0, (len(entries) - 1) // 25))
                batch = entries[s.page * 25 : (s.page + 1) * 25]
                selector = discord.ui.Select(
                    min_values=0,
                    max_values=len(batch),
                    placeholder="Select menus to customize together",
                    options=[
                        discord.SelectOption(
                            label=label[:100], value=key, default=key in s.selected
                        )
                        for key, label in batch
                    ],
                )

                async def chosen(i):
                    async with s.lock:
                        if not await s.allowed(i, self.generation):
                            return
                        await i.response.defer()
                        keys = {key for key, _ in batch}
                        await s.select(
                            [key for key in s.selected if key not in keys] + selector.values
                        )
                        await s.show(i, "targets")

                selector.callback = chosen
                panel.add_item(discord.ui.ActionRow(selector))
                for delta, label in ((-1, "Previous"), (1, "Next")):

                    async def turn(i, delta=delta):
                        s.page += delta
                        await s.show(i, "targets")

                    row(
                        label.upper(),
                        "Menu selection pages.",
                        label,
                        turn,
                        disabled=s.page == 0 if delta < 0 else (s.page + 1) * 25 >= len(entries),
                    )
            else:
                for key, label in FINISHES.items():

                    async def choose(i, key=key):
                        s.change(style=key)
                        await s.show(i)

                    row(
                        label.upper(), "Artwork, text and accent follow this finish.", label, choose
                    )
            row("RETURN TO EDITING", "Keep selected options.", "Back", edit)
        else:
            names = (", ".join(s.targets[key] for key in s.selected) or "None selected")[:1000]

            async def targets(i):
                await s.show(i, "targets")

            row("SELECT MENUS", safe(names), "Choose", targets)
            active = bool(s.selected)
            welcome = s.selected == ["welcome_image"]
            current = s.drafts[s.selected[0]] if active else default_profile("settings:overview")
            finishes = {s.drafts[key]["style"] for key in s.selected}

            async def finish(i):
                await s.show(i, "finish")

            row(
                "FINISH",
                FINISHES[next(iter(finishes))]
                if len(finishes) == 1
                else "Mixed finishes · choose to update all selected menus",
                "Choose",
                finish,
                disabled=not active,
            )
            row(
                "APPEARANCE",
                (
                    f"{safe(current['title'] or 'Default title')} · Darkening {current['dim']}%"
                    if welcome
                    else f"{safe(current['title'] or 'Each menu keeps its title')} · "
                    f"{current['height']}px · Footer: {safe(current['footer_text'] or 'Icon')}"
                ),
                "Edit",
                (None,),
                disabled=not active,
            )
            row(
                "BACKGROUND" if welcome else "HEADER BACKGROUND",
                ("Custom image" if current["header_background"] else "Floral artwork")
                + " · KY BOT corner branding stays in place.",
                "Upload",
                ("header_background",),
                disabled=not active,
            )
            if not welcome:
                row(
                    "FOOTER BACKGROUND",
                    "Replace the background image.",
                    "Upload",
                    ("footer_background",),
                    disabled=not active,
                )
                row(
                    "FOOTER ICON",
                    "Use a custom icon or emoji image.",
                    "Upload",
                    ("footer_icon",),
                    disabled=not active,
                )

                async def frame(i):
                    value = not s.drafts[s.selected[0]]["framed"]
                    s.change(framed=value)
                    await s.show(i)

                row(
                    "FRAME",
                    ("On" if current["framed"] else "Off")
                    + " · Matching header and footer outlines.",
                    "Turn on / off",
                    frame,
                    disabled=not active,
                )

            async def reset(i):
                for key in s.selected:
                    s.drafts[key] = default_profile(key)
                await s.show(
                    i, notice="Default artwork restored in this draft. Preview to approve."
                )

            row(
                "RESTORE DEFAULTS",
                "Changes stay in draft until approved.",
                "Restore",
                reset,
                disabled=not active,
            )

            async def preview(i):
                await s.preview(i, s.selected[0])

            row(
                "PREVIEW",
                "Private sample; its example buttons are disabled.",
                "Preview",
                preview,
                disabled=not active,
            )

        async def cancel(i):
            from ky_bot.admin_functions.settings_view import SettingsView

            menu = SettingsView(s.owner, s.guild_id, s.service)
            await menu.refresh(i)
            self.stop()

        row("CANCEL", "Discard this draft and return to server settings.", "Cancel", cancel)
        if sample and self.total_children_count + panel._total_count > 40:
            destination = self.children[-1]
            if not isinstance(destination, discord.ui.Container):
                raise ValueError("Preview exceeds Discord's component limit.")
            for item in list(panel.children):
                panel.remove_item(item)
                destination.add_item(item)
        else:
            self.add_item(panel)

    async def interaction_check(self, interaction):
        return await self.session.allowed(interaction, self.generation)

    async def on_error(self, interaction, error, item):
        await editor_error(interaction, error)
