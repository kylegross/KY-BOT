"""Administrator setup and a section/row editor using existing Command Center navigation."""

import copy

import discord

from ky_bot.confessions.command import destination
from ky_bot.confessions.menus import Artwork
from ky_bot.errors import report_error, send_private
from ky_bot.views.menu_context import display_menu, menu_parent
from ky_bot.views.sections import section_dividers

from .command import administrator
from .layout import arrange, attachments
from .panels import Panel, access_panel, artwork_bytes, submission_panel
from .store import defaults, validate

COLOR_NAMES = {
    "silver_neon": "Silver",
    "dark_silver": "Dark silver",
    "gold": "Gold",
    "red_iridescent": "Red",
}


class Edit(discord.ui.Modal):
    def __init__(self, menu, title, fields, callback):
        super().__init__(title=title, timeout=600)
        self.menu, self.revision, self.channel = menu, menu.revision, menu.channel
        self.selection = menu.section, menu.row
        self.callback, self.fields = callback, {}
        for key, label, value, limit, required, paragraph in fields:
            field = discord.ui.TextInput(
                label=label,
                default="" if value is None else str(value),
                max_length=limit,
                required=required,
                style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short,
            )
            self.fields[key] = field
            self.add_item(field)

    async def on_submit(self, i):
        try:
            await i.response.defer()
            await self.menu.allowed(i)
            if self.menu.channel != self.channel or self.menu.revision != self.revision:
                raise ValueError("Settings changed. Reopen this editor.")
            if (self.menu.section, self.menu.row) != self.selection:
                raise ValueError("The selected section or row changed. Reopen this editor.")
            current, _ = await self.menu.cog.store.config(i.guild_id, self.channel)
            if current != self.revision:
                raise ValueError("Another administrator changed settings. Refresh first.")
            await self.callback(i, {key: str(value) for key, value in self.fields.items()})
        except ValueError as error:
            await send_private(i, str(error))

    async def on_error(self, i, error):
        await report_error(i, error)


class AdviceArtwork(Artwork):
    def __init__(self, menu, kind):
        super().__init__(menu, kind)
        self.title = "Upload advice " + kind


class Admin(discord.ui.LayoutView):
    def __init__(self, cog, owner, guild, *, parent=None):
        super().__init__(timeout=600)
        self.cog, self.owner, self.guild, self.parent = cog, owner, guild, parent
        self.channel, self.revision, self.config = 0, None, defaults()
        self.page, self.section, self.row = "channels", 0, 0
        self.channel_page = 0

    async def allowed(self, i):
        if i.user.id != self.owner or i.guild_id != self.guild:
            raise ValueError("Open your own Ask for Advice settings.")
        await administrator(i)
        if self.parent and getattr(self.parent, "panel_manager", None):
            if not await self.parent.interaction_check(i):
                raise ValueError("Reopen Ask for Advice from the current Command Center.")

    async def interaction_check(self, i):
        try:
            await self.allowed(i)
            return True
        except ValueError as error:
            await send_private(i, str(error))
            return False

    async def on_error(self, i, error, item):
        if isinstance(error, ValueError):
            await send_private(i, str(error))
        else:
            await report_error(i, error)

    async def save(self, i, revision, **changes):
        await self.allowed(i)
        config = validate(self.config | changes)
        for key in ("access_channel", "log_channel", "queue_channel"):
            required = (key == "log_channel" and changes.get("logging") is True) or (
                key == "queue_channel"
                and any(changes.get(flag) is True for flag in ("approval", "response_approval"))
            )
            if config[key] and (key in changes or required):
                destination(i.guild, config[key], private=key != "access_channel")
        if config["enabled"] and (
            changes.get("enabled") is True
            or any(
                key in changes
                for key in ("access_channel", "access_role", "advice_role", "notifications")
            )
        ):
            await self.cog.validate_setup(i.guild, self.channel, config)
        self.revision, self.config = await self.cog.store.configure(
            self.guild, self.channel, revision, **changes
        )

    def button(self, label, callback, *, modal=False, disabled=False):
        button = discord.ui.Button(label=label, disabled=disabled)

        async def click(i):
            await self.allowed(i)
            if not modal:
                await i.response.defer()
            await callback(i)

        button.callback = click
        return button

    def navigate(self, page, label="Manage"):
        async def go(i):
            await self.show(i, page)

        return self.button(label, go)

    def picker(self, panel, item, callback, *, modal=False):
        async def choose(i):
            await self.allowed(i)
            if not modal:
                await i.response.defer()
            await callback(i, item.values)

        item.callback = choose
        panel.add_item(discord.ui.ActionRow(item))

    def image_status(self, kind):
        if self.config[kind]:
            return "Your uploaded image"
        palette = self.config.get(kind + "_palette")
        if palette:
            return "Custom colors · " + str(palette["base"])
        finish = self.config.get(kind + "_artwork_finish") or self.config["artwork_finish"]
        return "KY BOT artwork · " + COLOR_NAMES[finish]

    async def open_palette(self, i, kind):
        from ky_bot.design.palette_editor import PaletteModal

        await self.allowed(i)
        # The preview is private. Its callbacks must not be authorized as shared-panel clicks.
        private = Admin(self.cog, self.owner, self.guild)
        private.channel, private.revision = self.channel, self.revision
        private.config = copy.deepcopy(self.config)
        private.page = kind + "_artwork"
        revision, snapshot = private.revision, copy.deepcopy(private.config)

        async def allowed(event):
            await private.allowed(event)
            return True

        def render(palette):
            config = snapshot | {kind: None, kind + "_palette": palette}
            return [("advice_custom_" + kind + ".png", artwork_bytes(config, kind))]

        async def commit(event, palette):
            await event.response.defer(ephemeral=True)
            await private.save(event, revision, **{kind: None, kind + "_palette": palette})
            await private.show(
                event,
                private.page,
                f"{kind.title()} colors saved for <#{private.channel}>. "
                "Choose Publish changes to update the posted panels.",
            )

        await i.response.send_modal(
            PaletteModal(
                self.config.get(kind + "_palette"),
                render,
                commit,
                allowed,
                title="Choose " + kind + " colors",
                save_label="Save " + kind + " colors",
                field_labels={
                    "base": "Main hex color (for example #7B2CBF)",
                    "highlight": "Light color (blank = automatic)",
                    "shadow": "Dark color (blank = automatic)",
                    "accent": "Shimmer color (blank = automatic)",
                },
            )
        )

    def setting(self, panel, title, text, button):
        panel.add_item(discord.ui.Section(f"**{title}**\n-# {text}", accessory=button))

    def toggle(self, panel, key, title):
        async def change(i):
            await self.save(i, self.revision, **{key: not self.config[key]})
            await self.show(i, self.page)

        self.setting(
            panel,
            title,
            "On" if self.config[key] else "Off",
            self.button("Turn off" if self.config[key] else "Turn on", change),
        )

    def destination_picker(self, panel, key, title, page):
        panel.add_item(
            discord.ui.TextDisplay(
                f"**{title}**\n"
                + (f"<#{self.config[key]}>" if self.config[key] else "Not selected")
            )
        )

        async def choose(click, values):
            changes = {key: values[0].id if values else 0}
            if not values:
                changes.update(
                    {"logging": False}
                    if key == "log_channel"
                    else {
                        "approval": False,
                        "response_approval": False,
                        "reports": False,
                    }
                )
            await self.save(click, self.revision, **changes)
            await self.show(click, page)

        self.picker(
            panel,
            discord.ui.ChannelSelect(
                placeholder=title.title(),
                channel_types=[discord.ChannelType.text],
                min_values=0,
                default_values=[discord.Object(id=self.config[key])] if self.config[key] else [],
            ),
            choose,
        )

    async def edit_values(self, i, title, keys, *, page=None):
        # key, label, max length, required, paragraph
        fields = [
            (key, label, self.config[key], limit, required, paragraph)
            for key, label, limit, required, paragraph in keys
        ]

        async def save(click, values):
            for key in ("sticky_count", "cooldown", "max_length"):
                if key in values:
                    values[key] = int(values[key])
            for key in ("color", "text_color", "header_text_color"):
                if key in values:
                    values[key] = values[key].lstrip("#") or None
            if "header_text_color" in values:
                values["header_text_finish"] = None
            if self.config["header"] and (
                "header_title" in values or "header_text_color" in values
            ):
                values["header_title_on_upload"] = True
            await self.save(click, self.revision, **values)
            await self.show(click, page or self.page, "Saved.")

        await i.response.send_modal(Edit(self, title, fields, save))

    async def preview(self, i, access):
        view = (
            access_panel(self.channel, self.config, preview=True)
            if access
            else (submission_panel(self.channel, self.config, preview=True))
        )
        files = view.uploads()
        try:
            await i.followup.send(
                view=view,
                files=files,
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        finally:
            for file in files:
                file.close()

    async def show(self, i, page, notice=None):
        await self.allowed(i)
        self.parent = menu_parent(self.parent, i)
        self.page = page
        if self.channel:
            self.revision, self.config = await self.cog.store.config(self.guild, self.channel)
        self.clear_items()
        appearance_page = page in {"style", "artwork", "header_artwork", "footer_artwork"}
        menu_config = self.config | {"footer_text": ""} if appearance_page else defaults()
        view = Panel(menu_config)
        panel = view.panel
        if notice:
            panel.add_item(discord.ui.TextDisplay(notice))
        if page == "channels":
            saved = sorted(await self.cog.store.channels(self.guild), key=lambda item: item[1])
            if saved:
                self.channel_page = min(self.channel_page, (len(saved) - 1) // 25)
                options = []
                for _, channel_id, config in saved[
                    self.channel_page * 25 : self.channel_page * 25 + 25
                ]:
                    target = getattr(getattr(i, "guild", None), "get_channel", lambda _: None)(
                        channel_id
                    )
                    label = "#" + getattr(target, "name", str(channel_id))
                    options.append(
                        discord.SelectOption(
                            label=label[:100],
                            value=str(channel_id),
                            description="Enabled"
                            if config["enabled"]
                            else "Paused / setup in progress",
                        )
                    )
                panel.add_item(
                    discord.ui.TextDisplay(
                        "**SAVED ASK CHANNELS**\n"
                        "Choose a setup to manage its own roles, panels and settings."
                    )
                )

                async def select_saved(click, values):
                    self.channel = int(values[0])
                    self.section, self.row = 0, 0
                    await self.show(click, "main")

                self.picker(
                    panel,
                    discord.ui.Select(placeholder="Manage a saved ask channel", options=options),
                    select_saved,
                )
                if len(saved) > 25:
                    controls = discord.ui.ActionRow()
                    for delta, label in ((-1, "Previous channels"), (1, "Next channels")):

                        async def turn(click, delta=delta):
                            self.channel_page += delta
                            await self.show(click, "channels")

                        controls.add_item(
                            self.button(
                                label,
                                turn,
                                disabled=not (
                                    0 <= self.channel_page + delta <= (len(saved) - 1) // 25
                                ),
                            )
                        )
                    panel.add_item(controls)
            panel.add_item(
                discord.ui.TextDisplay(
                    "**ASK CHANNEL**\nChoose another ask channel to add a separate setup, "
                    "or select an existing channel to edit it. "
                    "Each has its own roles, access panel, approvals and appearance."
                )
            )

            async def select_channel(click, values):
                self.channel = values[0].id
                self.section, self.row = 0, 0
                revision, _ = await self.cog.store.config(self.guild, self.channel)
                if revision is None:
                    await self.cog.store.configure(self.guild, self.channel, None)
                await self.show(click, "main")

            self.picker(
                panel,
                discord.ui.ChannelSelect(
                    placeholder="Add or choose an ask channel",
                    channel_types=[discord.ChannelType.text],
                ),
                select_channel,
            )
        elif page == "main":
            panel.add_item(discord.ui.TextDisplay(f"Ask channel: <#{self.channel}>"))
            for title, text, target in (
                (
                    "CHANNELS & ROLES",
                    "Choose where members join and which roles the buttons give.",
                    "roles",
                ),
                (
                    "DELIVERY & PRIVACY",
                    "Choose log/approval channels, then turn the options you want on.",
                    "delivery",
                ),
                (
                    "APPEARANCE",
                    "Change the images, text and colors on your panels.",
                    "style",
                ),
                (
                    "ACCESS PANEL SECTIONS",
                    "Edit the text beside the four role buttons, or add/remove text blocks.",
                    "sections",
                ),
                (
                    "PREVIEW & PUBLISH",
                    "Check your panels, then post your saved changes to Discord.",
                    "publish",
                ),
                ("PENDING APPROVALS", "Review questions and responses privately.", "queue"),
            ):
                self.setting(panel, title, text, self.navigate(target))
            self.toggle(panel, "enabled", "ASK FOR ADVICE STATUS")
        elif page == "roles":
            panel.add_item(
                discord.ui.TextDisplay(
                    "**ACCESS & RESPONSE CHANNEL**\n"
                    "Select the channel everyone can see before joining."
                )
            )

            async def access_channel(click, values):
                await self.save(click, self.revision, access_channel=values[0].id)
                await self.show(click, "roles")

            self.picker(
                panel,
                discord.ui.ChannelSelect(
                    channel_types=[discord.ChannelType.text],
                    placeholder="Access & response channel",
                    default_values=[discord.Object(id=self.config["access_channel"])]
                    if self.config["access_channel"]
                    else [],
                ),
                access_channel,
            )
            for key, title in (
                ("access_role", "VIEW ADVICE ROLE"),
                ("advice_role", "GIVE ADVICE ROLE"),
            ):
                panel.add_item(
                    discord.ui.TextDisplay(
                        f"**{title}**\n"
                        + (f"<@&{self.config[key]}>" if self.config[key] else "Not selected")
                    )
                )

                async def choose_role(click, values, key=key):
                    await self.save(click, self.revision, **{key: values[0].id})
                    await self.show(click, "roles")

                self.picker(
                    panel,
                    discord.ui.RoleSelect(
                        placeholder=title.title(),
                        default_values=[discord.Object(id=self.config[key])]
                        if self.config[key]
                        else [],
                    ),
                    choose_role,
                )
            panel.add_item(
                discord.ui.TextDisplay(
                    "Join grants View Advice. Give Advice requires View Advice. "
                    "Leave removes both; Remove Advice Role removes only Give Advice. "
                    "Use dedicated roles without staff powers, and different roles for each "
                    "ask channel when membership should stay separate."
                )
            )
        elif page == "delivery":
            self.destination_picker(panel, "queue_channel", "APPROVAL CHANNEL", "delivery")
            self.destination_picker(panel, "log_channel", "AUTHOR LOG CHANNEL", "delivery")
            for key, title in (
                ("approval", "QUESTION APPROVAL"),
                ("response_approval", "ADVICE RESPONSE APPROVAL"),
                ("logging", "AUTHOR LOGGING"),
                ("notifications", "NEW QUESTION ROLE NOTIFICATIONS"),
                ("sticky", "REPOST SUBMISSION PANEL"),
            ):
                self.toggle(panel, key, title)

            async def limits(click):
                await self.edit_values(
                    click,
                    "Delivery and limits",
                    (
                        ("sticky_count", "Repost after messages (1–100)", 3, True, False),
                        ("cooldown", "Submission cooldown in seconds", 5, True, False),
                        ("max_length", "Maximum characters (1–2000)", 4, True, False),
                    ),
                )

            self.setting(
                panel,
                "LIMITS",
                f"Repost every {self.config['sticky_count']} messages; "
                "member and bot messages count.",
                self.button("Edit", limits, modal=True),
            )
        elif page == "destinations":
            for key, title in (
                ("queue_channel", "APPROVAL CHANNEL"),
                ("log_channel", "AUTHOR LOG CHANNEL"),
            ):
                self.destination_picker(panel, key, title, "destinations")
        elif page == "style":
            self.setting(
                panel,
                "HEADER & FOOTER",
                "Choose the top banner and bottom image separately.",
                self.navigate("artwork"),
            )

            async def style(click):
                await self.edit_values(
                    click,
                    "Panel appearance",
                    (
                        ("color", "Side highlight hex color", 6, True, False),
                        ("text_color", "Panel heading hex color (blank = preset)", 7, False, False),
                        ("footer_text", "Small footer line below the message", 200, False, False),
                        ("title", "Published question heading", 80, True, False),
                        ("response_title", "Published advice heading", 80, True, False),
                    ),
                )

            self.setting(
                panel,
                "PANEL TEXT & HIGHLIGHT",
                f"Accent: #{self.config['color']}",
                self.button("Edit", style, modal=True),
            )
            panel.add_item(
                discord.ui.TextDisplay(
                    "**PANEL HEADING COLORS**\nColor of headings inside the panels. "
                    "The header image title has its own color setting."
                )
            )

            async def finish(click, values):
                if values[0] == "custom":
                    return await self.edit_values(
                        click,
                        "Panel heading color",
                        (("text_color", "Hex color (for example #39DCCB)", 7, True, False),),
                    )
                await click.response.defer()
                await self.save(click, self.revision, text_finish=values[0], text_color=None)
                await self.show(click, "style", "Panel heading color saved.")

            self.picker(
                panel,
                discord.ui.Select(
                    placeholder="Choose panel heading colors",
                    options=[
                        *[
                            discord.SelectOption(
                                label=label,
                                value=key,
                                default=key == self.config["text_finish"]
                                and not self.config["text_color"],
                            )
                            for key, label in COLOR_NAMES.items()
                        ],
                        discord.SelectOption(
                            label="Custom color…",
                            value="custom",
                            default=bool(self.config["text_color"]),
                        ),
                    ],
                ),
                finish,
                modal=True,
            )

            async def text(click):
                await self.edit_values(
                    click,
                    "Ask channel submission panel",
                    (
                        ("panel_title", "Heading", 80, True, False),
                        ("panel_text", "Description", 1000, False, True),
                        ("submit_label", "Submit button label", 80, True, False),
                    ),
                )

            self.setting(
                panel,
                "ASK CHANNEL PANEL",
                "Edit the anonymous question prompt.",
                self.button("Edit", text, modal=True),
            )
            self.setting(
                panel,
                "ACCESS PANEL TEXT",
                "Headings, descriptions, button labels and emojis.",
                self.navigate("sections", "Edit sections"),
            )
        elif page == "artwork":
            panel.add_item(
                discord.ui.TextDisplay(
                    "Choose the image you want to change. Changes save to this ask channel. "
                    "Preview them here, then publish when you're ready."
                )
            )
            for kind in ("header", "footer"):
                self.setting(
                    panel,
                    kind.upper() + " IMAGE",
                    self.image_status(kind),
                    self.navigate(kind + "_artwork", "Edit " + kind),
                )
            self.setting(
                panel,
                "PREVIEW PANELS",
                "See both saved images on the member-facing panel.",
                self.button("Preview", lambda click: self.preview(click, True)),
            )
            self.setting(
                panel,
                "PUBLISH CHANGES",
                "Update the panels already posted in Discord.",
                self.navigate("publish", "Publish changes"),
            )
        elif page in {"header_artwork", "footer_artwork"}:
            kind = "header" if page == "header_artwork" else "footer"
            panel.add_item(
                discord.ui.TextDisplay(
                    f"Editing the **{kind} image** for <#{self.channel}>. "
                    "Saved changes appear above/below. Publish them when you're ready."
                )
            )
            panel.add_item(discord.ui.TextDisplay("**IMAGE SOURCE**\n" + self.image_status(kind)))

            async def upload(click):
                await click.response.send_modal(AdviceArtwork(self, kind))

            self.setting(
                panel,
                "UPLOAD IMAGE",
                "Use your own PNG, JPEG or WebP image.",
                self.button("Upload image", upload, modal=True),
            )
            panel.add_item(
                discord.ui.TextDisplay(
                    "**IMAGE COLORS**\nChoose KY BOT artwork colors, or choose Custom colors. "
                    "This replaces only this image; the other image stays unchanged."
                )
            )
            current = self.config.get(kind + "_artwork_finish") or self.config["artwork_finish"]
            custom = bool(self.config.get(kind + "_palette")) and not self.config[kind]
            picker = discord.ui.Select(
                placeholder="Choose colors for this image",
                options=[
                    *[
                        discord.SelectOption(
                            label=label,
                            value=key,
                            default=key == current and not custom and not self.config[kind],
                        )
                        for key, label in COLOR_NAMES.items()
                    ],
                    discord.SelectOption(label="Custom colors…", value="custom", default=custom),
                ],
            )

            async def image_colors(click, values):
                if values[0] == "custom":
                    return await self.open_palette(click, kind)
                await click.response.defer()
                await self.save(
                    click,
                    self.revision,
                    **{
                        kind: None,
                        kind + "_palette": None,
                        kind + "_artwork_finish": values[0],
                    },
                )
                await self.show(
                    click,
                    page,
                    f"{kind.title()} colors saved. "
                    "Choose Publish changes to update the posted panels.",
                )

            self.picker(panel, picker, image_colors, modal=True)
            if kind == "header":

                async def title(click):
                    await self.edit_values(
                        click,
                        "Header title text",
                        (("header_title", "Text inside the header image", 80, True, False),),
                    )

                self.setting(
                    panel,
                    "TITLE TEXT",
                    self.config["header_title"]
                    + " · Text added by KY BOT; keeps a metallic shine.",
                    self.button("Edit title", title, modal=True),
                )

                async def title_color(click):
                    await self.edit_values(
                        click,
                        "Header title color",
                        (
                            (
                                "header_text_color",
                                "Hex color (blank uses artwork colors)",
                                7,
                                False,
                                False,
                            ),
                        ),
                    )

                color = self.config["header_text_color"]
                legacy = self.config["header_text_finish"]
                status = color or (COLOR_NAMES[legacy] if legacy else "Uses the artwork colors")
                self.setting(
                    panel,
                    "TITLE COLOR",
                    status + " · Blank uses the image's colors.",
                    self.button("Change color", title_color, modal=True),
                )
                if self.config["header"]:
                    self.toggle(panel, "header_title_on_upload", "TITLE ON YOUR UPLOADED IMAGE")
            else:

                async def footer_text(click):
                    await self.edit_values(
                        click,
                        "Footer image text and color",
                        (
                            (
                                "footer_image_text",
                                "Text on footer image (blank removes)",
                                80,
                                False,
                                False,
                            ),
                            (
                                "footer_text_color",
                                "Metallic text hex (blank uses image colors)",
                                7,
                                False,
                                False,
                            ),
                        ),
                    )

                self.setting(
                    panel,
                    "FOOTER IMAGE TEXT",
                    (self.config["footer_image_text"] or "No text added")
                    + " · Works on KY BOT artwork and your uploaded image.",
                    self.button("Edit text & color", footer_text, modal=True),
                )

            async def reset(click):
                changes = {
                    kind: None,
                    kind + "_palette": None,
                    kind + "_artwork_finish": "silver_neon",
                }
                if kind == "header":
                    changes.update(
                        header_text_color=None,
                        header_text_finish=None,
                        header_title_on_upload=False,
                    )
                else:
                    changes.update(footer_image_text="", footer_text_color=None)
                await self.save(click, self.revision, **changes)
                await self.show(
                    click, page, f"Default {kind} restored. The other image is unchanged."
                )

            self.setting(
                panel,
                "RESET IMAGE",
                "Return this image to the default silver KY BOT design.",
                self.button("Reset " + kind, reset),
            )
            self.setting(
                panel,
                "PREVIEW PANELS",
                "Preview the saved images and panel text together.",
                self.button("Preview", lambda click: self.preview(click, True)),
            )
            self.setting(
                panel,
                "PUBLISH CHANGES",
                "Saving does not update posted panels until you publish.",
                self.navigate("publish", "Publish changes"),
            )
        elif page in {"sections", "section", "row"}:
            await self.section_page(i, panel, page)
        elif page == "publish":
            for access, label in ((True, "Access panel"), (False, "Ask channel panel")):

                async def preview(click, access=access):
                    await self.preview(click, access)

                async def publish(click, access=access):
                    await self.cog.publish_panel(click.guild, self.channel, access=access)
                    await self.show(click, "publish", "Panel published.")

                self.setting(
                    panel,
                    label.upper(),
                    "Private preview with disabled buttons.",
                    self.button("Preview", preview),
                )
                self.setting(
                    panel,
                    "PUBLISH " + label.upper(),
                    "Replaces the previous published panel.",
                    self.button("Publish", publish),
                )

            async def permissions(click):
                await self.cog.apply_access(click.guild, self.channel, self.config)
                await self.show(click, "publish", "Channel access configured.")

            self.setting(
                panel,
                "CONFIGURE CHANNEL VISIBILITY",
                "Hide the ask channel from @everyone; "
                "grant View Channel to the selected access role. Other overwrites are preserved.",
                self.button("Apply access", permissions),
            )
        elif page == "queue":
            # Keep submission text and author identities off the shared settings panel.
            panel.add_item(discord.ui.TextDisplay("**PENDING APPROVALS**"))
            posts = await self.cog.store.pending(self.guild, self.channel)
            if posts:
                options = [
                    discord.SelectOption(
                        label=("Advice for" if p["parent"] else "Question")
                        + f" #{p['data']['number']} · {p['id'][:6]}",
                        value=p["id"],
                    )
                    for p in posts
                ]

                async def review(click, values):
                    from .panels import post_panel

                    post = await self.cog.store.post(values[0])
                    if not post or post["state"] != "pending":
                        raise ValueError("This submission was already reviewed. Refresh the queue.")
                    view = post_panel(post, self.config, review=True)
                    files = view.uploads()
                    try:
                        await click.followup.send(
                            view=view,
                            files=files,
                            ephemeral=True,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    finally:
                        for file in files:
                            file.close()

                self.picker(
                    panel,
                    discord.ui.Select(
                        placeholder="Review a pending submission privately", options=options
                    ),
                    review,
                )
                panel.add_item(
                    discord.ui.TextDisplay(
                        "Showing up to 25 pending submissions. Refresh after reviewing to see more."
                    )
                )
            else:
                panel.add_item(discord.ui.TextDisplay("No pending questions or advice responses."))
        panel.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
        controls = discord.ui.ActionRow()
        if page != "channels":
            previous = {
                "main": "channels",
                "destinations": "delivery",
                "artwork": "style",
                "header_artwork": "artwork",
                "footer_artwork": "artwork",
                "section": "sections",
                "row": "section",
            }.get(page, "main")
            controls.add_item(self.navigate(previous, "Back"))
        controls.add_item(self.navigate(page, "Refresh"))
        if self.parent:

            async def home(click):
                from ky_bot.admin_functions.settings_view import settings_files

                self.parent.show_section("overview")
                files = settings_files("overview", self.parent.design)
                try:
                    await display_menu(click, self.parent, files, self.parent)
                finally:
                    for file in files:
                        file.close()

            controls.add_item(self.button("Home", home))
        panel.add_item(controls)
        view.footer(menu_config)
        arrange(panel, page)
        section_dividers(panel, navigation_on_right=True)
        # Transfer the rendered container to this owner-checked settings view.
        view.remove_item(panel)
        self.add_item(panel)
        files = view.uploads() + attachments(page)
        try:
            await display_menu(i, self, files, self.parent)
        finally:
            for file in files:
                file.close()

    async def section_page(self, i, panel, page):
        sections = self.config["sections"]
        self.section = min(self.section, len(sections) - 1)
        section = sections[self.section]
        self.row = min(self.row, len(section["rows"]) - 1)

        async def store(click, values, target=page):
            await self.save(click, self.revision, sections=values)
            await self.show(click, target)

        if page == "sections":
            panel.add_item(discord.ui.TextDisplay("**PANEL SECTIONS**"))

            async def select(click, values):
                self.section, self.row = int(values[0]), 0
                await self.show(click, "section")

            self.picker(
                panel,
                discord.ui.Select(
                    placeholder="Choose a section",
                    options=[
                        discord.SelectOption(
                            label=f"{index + 1}. {s['heading'] or 'Untitled'}"[:100],
                            value=str(index),
                        )
                        for index, s in enumerate(sections)
                    ],
                ),
                select,
            )

            async def add(click):
                values = copy.deepcopy(sections)
                values.append(
                    dict(
                        heading="NEW SECTION",
                        decorated=False,
                        rows=[
                            dict(
                                action="none", label="", emoji="", text="Add your description here."
                            )
                        ],
                    )
                )
                await store(click, values)

            self.setting(
                panel,
                "ADD SECTION",
                "Up to 4 sections and 6 text rows in total.",
                self.button("Add section", add, disabled=len(sections) >= 4),
            )
            return
        if page == "section":

            async def heading(click):
                fields = [
                    (
                        "heading",
                        "Section heading (blank hides it)",
                        section["heading"],
                        80,
                        False,
                        False,
                    )
                ]

                async def save(edit, values):
                    updated = copy.deepcopy(sections)
                    updated[self.section]["heading"] = values["heading"]
                    await store(edit, updated)

                await click.response.send_modal(Edit(self, "Section heading", fields, save))

            self.setting(
                panel,
                "HEADING",
                section["heading"] or "Hidden",
                self.button("Edit", heading, modal=True),
            )

            async def decoration(click):
                values = copy.deepcopy(sections)
                values[self.section]["decorated"] = not section["decorated"]
                await store(click, values)

            self.setting(
                panel,
                "DECORATIVE SIDE LINES",
                "On" if section["decorated"] else "Off",
                self.button("Toggle", decoration),
            )

            async def select(click, values):
                self.row = int(values[0])
                await self.show(click, "row")

            panel.add_item(discord.ui.TextDisplay("**TEXT ROWS**"))
            self.picker(
                panel,
                discord.ui.Select(
                    placeholder="Edit text/button row",
                    options=[
                        discord.SelectOption(
                            label=f"{index + 1}. {row['label'] or row['text']}"[:100],
                            value=str(index),
                        )
                        for index, row in enumerate(section["rows"])
                    ],
                ),
                select,
            )

            async def add(click):
                values = copy.deepcopy(sections)
                values[self.section]["rows"].append(
                    dict(action="none", label="", emoji="", text="Add your description here.")
                )
                await store(click, values)

            self.setting(
                panel,
                "ADD TEXT ROW",
                "Optional text, with or without a role button.",
                self.button("Add row", add, disabled=len(section["rows"]) >= 4),
            )
        else:
            row = section["rows"][self.row]

            async def text(click):
                fields = [
                    (key, label, row[key], limit, required, paragraph)
                    for key, label, limit, required, paragraph in (
                        ("text", "Description", 1000, True, True),
                        ("label", "Button label", 80, False, False),
                        ("emoji", "Button emoji (optional)", 100, False, False),
                    )
                ]

                async def save(edit, values):
                    updated = copy.deepcopy(sections)
                    updated[self.section]["rows"][self.row].update(values)
                    await store(edit, updated)

                await click.response.send_modal(Edit(self, "Text and button", fields, save))

            self.setting(
                panel, "ROW TEXT & BUTTON", row["text"][:200], self.button("Edit", text, modal=True)
            )

            async def action(click, values):
                updated = copy.deepcopy(sections)
                updated[self.section]["rows"][self.row]["action"] = values[0]
                await store(click, updated)

            panel.add_item(discord.ui.TextDisplay("**BUTTON ACTION**"))
            self.picker(
                panel,
                discord.ui.Select(
                    placeholder="Button action",
                    options=[
                        discord.SelectOption(label=label, value=key, default=key == row["action"])
                        for key, label in (
                            ("none", "Text only"),
                            ("join", "Join: grant access"),
                            ("leave", "Leave: remove both roles"),
                            ("give", "Give Advice: grant advice role"),
                            ("ungive", "Remove advice role only"),
                        )
                    ],
                ),
                action,
            )
        for delta, label in ((-1, "Move up"), (1, "Move down")):
            index = self.section if page == "section" else self.row
            length = len(sections) if page == "section" else len(section["rows"])

            async def move(click, delta=delta):
                values = copy.deepcopy(sections)
                items = values if page == "section" else values[self.section]["rows"]
                index = self.section if page == "section" else self.row
                items[index], items[index + delta] = items[index + delta], items[index]
                if page == "section":
                    self.section += delta
                else:
                    self.row += delta
                await store(click, values)

            self.setting(
                panel,
                label.upper(),
                "Change display order.",
                self.button(
                    label,
                    move,
                    disabled=not 0 <= index + delta < length,
                ),
            )

        async def remove(click):
            values = copy.deepcopy(sections)
            if page == "section":
                values.pop(self.section)
            else:
                values[self.section]["rows"].pop(self.row)
            await store(click, values, "sections" if page == "section" else "section")

        self.setting(
            panel,
            "REMOVE",
            "Remove this section or row from the panel.",
            self.button(
                "Remove",
                remove,
                disabled=(len(sections) <= 1 if page == "section" else len(section["rows"]) <= 1),
            ),
        )
