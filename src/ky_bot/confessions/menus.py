"""Private inline setup, per-channel controls and administrator review menus."""

import asyncio
import base64
import io

import discord
from PIL import Image

from ky_bot.errors import report_error, send_private
from ky_bot.views.media import NoAltMediaGalleryItem
from ky_bot.views.menu_context import defer_menu, display_menu, menu_parent, private_parent
from ky_bot.views.sections import section_dividers

from .appearance import standard_artwork
from .cards import Card, post_card, safe, uploads
from .command import Action, administrator, destination
from .headings import DIVIDER
from .layout import arrange, attachments


class ChannelChooser(discord.ui.View):
    def __init__(self, cog, interaction, channels):
        super().__init__(timeout=300)
        self.owner, self.guild = interaction.user.id, interaction.guild_id
        self.page = 0
        self.channels = channels
        self.cog = cog
        self.build(interaction.guild)

    def build(self, guild):
        self.clear_items()
        options = [
            discord.SelectOption(label=guild.get_channel(c).name, value=str(c))
            for _, c, _ in self.channels[self.page * 25 : (self.page + 1) * 25]
        ]
        picker = discord.ui.Select(placeholder="Choose your confession channel", options=options)

        async def choose(i):
            try:
                await self.cog.begin(i, int(picker.values[0]))
            except ValueError as error:
                await send_private(i, str(error))

        picker.callback = choose
        self.add_item(picker)
        for delta, label in ((-1, "Previous"), (1, "Next")):
            button = discord.ui.Button(
                label=label,
                disabled=self.page == 0
                if delta < 0
                else (self.page + 1) * 25 >= len(self.channels),
            )

            async def turn(i, delta=delta):
                self.page += delta
                self.build(i.guild)
                await i.response.edit_message(view=self)

            button.callback = turn
            self.add_item(button)

    async def interaction_check(self, i):
        if i.user.id == self.owner and i.guild_id == self.guild:
            return True
        await send_private(i, "Open your own confession channel picker.")
        return False


class Edit(discord.ui.Modal):
    def __init__(self, menu, kind):
        super().__init__(
            title={
                "style": "Confession appearance",
                "text": "Submission panel text",
                "header": "Header title",
                "lettercolor": "Custom decorative text color",
                "limits": "Submission limits",
                "sticky": "Sticky submission panel",
            }[kind]
        )
        self.menu, self.kind, self.revision, self.channel = menu, kind, menu.revision, menu.channel
        self.return_page = menu.page
        fields = {
            "header": (("header_title", "Header banner title", 80),),
            "lettercolor": (("text_color", "Hex color (blank follows selected finish)", 7),),
            "style": (
                ("color", "Embed color (hex, e.g. D6E2F3)", 6),
                ("title", "Confession heading", 80),
                ("footer_text", "Footer text", 200),
            ),
            "text": (
                ("panel_title", "Submission panel heading", 80),
                ("panel_text", "Submission panel message", 1000),
                ("submit_label", "Submit button label", 80),
            ),
            "limits": (
                ("cooldown", "Cooldown in seconds (0–86400)", 5),
                ("max_length", "Maximum characters (1–2000)", 4),
            ),
            "sticky": (("sticky_count", "Move panel after this many messages (1–100)", 3),),
        }[kind]
        self.fields = {}
        for key, label, limit in fields:
            item = discord.ui.TextInput(
                label=label,
                default="" if menu.config[key] is None else str(menu.config[key]),
                max_length=limit,
                required=key not in {"footer_text", "panel_text", "text_color"},
                style=discord.TextStyle.paragraph
                if key == "panel_text"
                else discord.TextStyle.short,
            )
            self.fields[key] = item
            self.add_item(item)

    async def on_submit(self, i):
        await i.response.defer(ephemeral=True)
        try:
            await self.menu.allowed(i)
            if self.menu.channel != self.channel:
                raise ValueError("The selected channel changed. Reopen this form.")
            values = {k: str(v) for k, v in self.fields.items()}
            for key in ("cooldown", "max_length", "sticky_count"):
                if key in values:
                    values[key] = int(values[key])
            if "color" in values:
                values["color"] = values["color"].lstrip("#")
            await self.menu.save(i, self.revision, **values)
            target = self.return_page
            await self.menu.show(i, target, "Settings saved for this channel only.")
        except ValueError as error:
            await send_private(i, str(error))

    async def on_error(self, i, error):
        await report_error(i, error)


class Artwork(discord.ui.Modal):
    def __init__(self, menu, kind):
        super().__init__(title="Upload confession " + kind)
        self.menu, self.kind, self.revision, self.channel = menu, kind, menu.revision, menu.channel
        self.return_page = menu.page
        self.upload = discord.ui.FileUpload(max_values=1, required=True)
        self.add_item(
            discord.ui.Label(text="Image (PNG, JPEG or WebP; up to 8 MB)", component=self.upload)
        )

    async def on_submit(self, i):
        await defer_menu(i, self.menu.parent)
        try:
            await self.menu.allowed(i)
            if self.menu.channel != self.channel:
                raise ValueError("The selected channel changed. Reopen the upload form.")
            file = self.upload.values[0]
            if file.size > 8 * 1024 * 1024:
                raise ValueError("Upload a still image up to 8 MB.")
            data = await file.read()

            def normalize():
                from ky_bot.design.footers import read_upload

                image = read_upload(data)
                image.thumbnail(
                    (1600, 500 if self.kind == "header" else 250), Image.Resampling.LANCZOS
                )
                stream = io.BytesIO()
                image.save(stream, format="PNG", optimize=True)
                if stream.tell() > 1_400_000:
                    raise ValueError("This image is too complex. Upload a smaller image.")
                return base64.b64encode(stream.getvalue()).decode("ascii")

            value = await asyncio.to_thread(normalize)
            await self.menu.save(i, self.revision, **{self.kind: value})
            await self.menu.show(i, self.return_page, "Artwork saved.")
        except (ValueError, OSError) as error:
            await send_private(i, str(error))

    async def on_error(self, i, error):
        await report_error(i, error)


class BanMember(discord.ui.Modal):
    def __init__(self, menu, remove=False):
        super().__init__(title="Lift confession ban" if remove else "Ban from confessions")
        self.menu, self.remove = menu, remove
        self.member = discord.ui.UserSelect(placeholder="Choose a member", required=True)
        self.reason = discord.ui.TextInput(label="Reason", required=not remove, max_length=400)
        self.add_item(discord.ui.Label(text="Member", component=self.member))
        self.add_item(self.reason)

    async def on_submit(self, i):
        await i.response.defer(ephemeral=True)
        try:
            await self.menu.allowed(i)
            member = self.member.values[0].id
            await self.menu.cog.store.set_ban(
                i.guild_id, i.user.id, str(self.reason), member=member, remove=self.remove
            )
            await self.menu.show(
                i,
                "bans",
                "Confession ban removed."
                if self.remove
                else "Member banned from confessions; server membership is unchanged.",
            )
        except ValueError as error:
            await send_private(i, str(error))


class Admin(discord.ui.LayoutView):
    def __init__(self, cog, owner, guild, *, parent=None):
        super().__init__(timeout=600)
        self.cog, self.owner, self.guild, self.parent = cog, owner, guild, parent
        self.channel = 0
        self.config = None
        self.revision = None
        self.lock = asyncio.Lock()
        self.page = "channels"
        self.start_logging = False
        self.new_private_message = False

    async def allowed(self, i):
        if i.user.id != self.owner or i.guild_id != self.guild:
            raise ValueError("Open your own Confessions settings menu.")
        await administrator(i)
        if self.parent and getattr(self.parent, "panel_manager", None):
            if not await self.parent.interaction_check(i):
                raise ValueError("Reopen Confessions from the current Command Center panel.")

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
        _, old = await self.cog.store.config(self.guild, self.channel)
        config = old | changes
        destination(i.guild, self.channel)
        # Set up destinations independently. Validate a destination when changing
        # it or enabling its feature, rather than blocking on another unset field.
        for key, flags, label in (
            ("queue_channel", ("approval", "reports"), "Approval & report channel"),
            ("log_channel", ("logging",), "Author log channel"),
        ):
            required = any(changes.get(flag) is True for flag in flags) or (
                changes.get("enabled") is True and any(config[flag] for flag in flags)
            )
            if key in changes or required:
                if not config[key]:
                    if required or any(config[flag] for flag in flags):
                        raise ValueError(f"Choose the {label} first in Logging & Approval.")
                    continue
                try:
                    destination(i.guild, config[key], private=True)
                except ValueError as error:
                    raise ValueError(f"{label}: {error}") from None
        self.revision, self.config = await self.cog.store.configure(
            self.guild, self.channel, revision, **changes
        )

    def button(self, label, action):
        item = discord.ui.Button(label=label)

        async def click(i):
            async with self.lock:
                await self.allowed(i)
                if isinstance(action, tuple):
                    kind = action[0]
                    modal = (
                        Artwork(self, kind.split(":")[1])
                        if kind.startswith("art:")
                        else BanMember(self, kind == "unban")
                        if kind in {"ban", "unban"}
                        else Edit(self, kind)
                    )
                    await i.response.send_modal(modal)
                else:
                    await i.response.defer()
                    await action(i)

        item.callback = click
        return item

    def go(self, page):
        async def navigate(i):
            await self.show(i, page)

        return navigate

    async def show(self, i, page, notice=None):
        from .store import defaults

        self.parent = menu_parent(self.parent, i)
        await self.allowed(i)
        if page in {"queue", "reports"} and self.parent and self.parent.panel_manager:
            # Never put queue bodies or identities onto the shared settings panel.
            if not i.response.is_done():
                await i.response.defer()
            private = Admin(self.cog, self.owner, self.guild, parent=private_parent(self.parent, i))
            private.channel = self.channel
            private.start_logging = self.start_logging
            private.new_private_message = True
            await private.show(i, page, notice)
            return
        self.page = page
        if self.channel:
            self.revision, self.config = await self.cog.store.config(self.guild, self.channel)
        config = self.config or defaults()
        # Channel appearance belongs to the published panel/cards, not its editor.
        menu_style = defaults()
        self.clear_items()
        panel = discord.ui.Container(accent_colour=int(menu_style["color"], 16))
        panel.add_item(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://confession_header.png"))
        )
        heading = {
            "channels": "CHANNELS",
            "channel": "CHANNEL SETTINGS",
            "behavior": "SUBMISSIONS & DELIVERY",
            "logging": "LOGGING & APPROVAL",
            "appearance": "APPEARANCE",
            "artwork": "HEADER & FOOTER DESIGN",
            "lettering": "DECORATIVE TEXT",
            "permissions": "ROLE ACCESS",
            "queue": "ADMINISTRATOR APPROVAL",
            "reports": "REPORTS",
            "bans": "CONFESSION BANS",
        }[page]
        self.header_art = standard_artwork(
            "header",
            menu_style["artwork_finish"],
            "CONFESSIONS" + DIVIDER + heading,
            menu_style["text_finish"],
            menu_style["text_color"],
        )
        panel.add_item(
            discord.ui.TextDisplay(
                "Administrators only · Authors are identified before publishing."
                if page == "queue"
                else "Review member reports privately."
                if page == "reports"
                else f"Configure <#{self.channel}>. Changes are saved per channel."
                if self.channel
                else "Anonymous member submissions. Configure each channel independently."
            )
        )
        if notice:
            panel.add_item(discord.ui.TextDisplay(notice))

        def row(title, text, label, action):
            panel.add_item(
                discord.ui.Section(
                    "**" + title + "**\n-# " + text, accessory=self.button(label, action)
                )
            )

        def toggle(key):
            revision = self.revision

            async def flip(i):
                await self.save(i, revision, **{key: not self.config[key]})
                await self.show(i, page)

            return flip

        if page == "channels":
            panel.add_item(discord.ui.TextDisplay("**CONFESSION CHANNELS**"))
            rows = await self.cog.store.channels(self.guild)
            picker = discord.ui.ChannelSelect(
                placeholder="Add or manage a confession channel",
                channel_types=[discord.ChannelType.text],
                min_values=1,
                max_values=1,
            )

            async def choose(event):
                await self.allowed(event)
                self.channel = picker.values[0].id
                destination(event.guild, self.channel)
                await event.response.defer(ephemeral=True)
                await self.show(event, "logging" if self.start_logging else "channel")

            picker.callback = choose
            panel.add_item(discord.ui.ActionRow(picker))
            if rows:
                panel.add_item(
                    discord.ui.TextDisplay(
                        "\n".join(
                            (
                                f"<#{channel}> · {'On' if values['enabled'] else 'Paused'} · "
                                f"#{values['color']} · Approval "
                                f"{'on' if values['approval'] else 'off'}"
                            )
                            for _, channel, values in rows
                        )[:3500]
                    )
                )
        elif page == "channel":
            row(
                "CONFESSIONS",
                "On" if config["enabled"] else "Paused",
                "Turn off" if config["enabled"] else "Turn on",
                toggle("enabled"),
            )
            row(
                "APPEARANCE",
                "Channel color, header/footer artwork and panel text.",
                "Manage",
                self.go("appearance"),
            )
            row(
                "LOGGING & APPROVAL",
                "Named author logs and administrator-only review.",
                "Manage",
                self.go("logging"),
            )
            row(
                "SUBMISSIONS & DELIVERY",
                "Replies, reports, sticky panel, limits and access.",
                "Manage",
                self.go("behavior"),
            )

            async def publish(event):
                await self.cog.publish_panel(event.guild, self.channel)
                await self.show(event, "channel", "Submission panel published/refreshed.")

            row(
                "SUBMISSION PANEL",
                "Publish the entry panel in this channel.",
                "Publish panel",
                publish,
            )
        elif page == "behavior":
            row(
                "ANONYMOUS REPLIES",
                "On" if config["replies"] else "Off",
                "Turn off" if config["replies"] else "Turn on",
                toggle("replies"),
            )
            row(
                "REPORTING",
                "On" if config["reports"] else "Off · Set a private review channel first.",
                "Turn off" if config["reports"] else "Turn on",
                toggle("reports"),
            )
            row(
                "STICKY SUBMISSION PANEL",
                (
                    f"{'On' if config['sticky'] else 'Off'} · "
                    f"Move after {config['sticky_count']} member messages."
                ),
                "Turn off" if config["sticky"] else "Turn on",
                toggle("sticky"),
            )
            row(
                "STICKY FREQUENCY",
                "Only the submission panel moves. Confessions stay in order.",
                "Edit",
                ("sticky",),
            )
            row(
                "LIMITS",
                f"{config['cooldown']}s cooldown · {config['max_length']} characters.",
                "Edit",
                ("limits",),
            )
            row(
                "ROLE ACCESS",
                "Allowed and blocked roles, checked at submission.",
                "Manage",
                self.go("permissions"),
            )
        elif page == "logging":
            row(
                "AUTHOR LOGGING",
                ("On · " + f"<#{config['log_channel']}>")
                if config["logging"]
                else "Off · Public posts are always anonymous.",
                "Turn off" if config["logging"] else "Turn on",
                toggle("logging"),
            )
            row(
                "ADMINISTRATOR APPROVAL",
                "On · Reviewers see the author before publishing."
                if config["approval"]
                else "Off · Publish after the member confirms.",
                "Turn off" if config["approval"] else "Turn on",
                toggle("approval"),
            )
            for key, label in (
                ("log_channel", "Author log channel"),
                ("queue_channel", "Approval & report channel"),
            ):
                panel.add_item(
                    discord.ui.TextDisplay(
                        "**"
                        + label.upper()
                        + "**\n-# "
                        + (f"<#{config[key]}>" if config[key] else "Not selected")
                        + "\n-# Select another channel, or clear × to remove it."
                    )
                )
                picker = discord.ui.ChannelSelect(
                    placeholder=label,
                    channel_types=[discord.ChannelType.text],
                    min_values=0,
                    default_values=[discord.Object(id=config[key])] if config[key] else [],
                )
                revision = self.revision

                async def choose(event, key=key, picker=picker, revision=revision):
                    await event.response.defer(ephemeral=True)
                    selected = picker.values[0].id if picker.values else 0
                    changes = {key: selected}
                    if not selected:
                        changes.update(
                            {"logging": False}
                            if key == "log_channel"
                            else {"approval": False, "reports": False}
                        )
                    await self.save(event, revision, **changes)
                    await self.show(
                        event,
                        "logging",
                        "Private destination saved."
                        if selected
                        else "Destination removed; its dependent features are off.",
                    )

                picker.callback = choose
                panel.add_item(discord.ui.ActionRow(picker))
            row(
                "PENDING APPROVALS",
                "Only administrators can view authors and approve or reject.",
                "Open queue",
                self.go("queue"),
            )
            row(
                "REPORTS",
                "Review reports, remove confessions or restrict authors.",
                "Open reports",
                self.go("reports"),
            )
            row(
                "CONFESSION BANS",
                "Server-wide confession restrictions; server membership is unchanged.",
                "Manage",
                self.go("bans"),
            )
        elif page in {"appearance", "artwork", "lettering"}:
            if page in {"artwork", "lettering"}:
                from ky_bot.design.menu_design import FINISHES

                key = "artwork_finish" if page == "artwork" else "text_finish"
                picker = discord.ui.Select(
                    placeholder="Choose a standard finish",
                    options=[
                        discord.SelectOption(
                            label=label,
                            value=value,
                            default=config[key] == value
                            and not (page == "lettering" and config["text_color"]),
                        )
                        for value, label in FINISHES.items()
                    ],
                )
                if page == "lettering":
                    picker.append_option(
                        discord.SelectOption(
                            label="Custom color…",
                            value="__custom",
                            default=bool(config["text_color"]),
                        )
                    )
                revision, selected_channel = self.revision, self.channel

                async def choose_finish(event):
                    await self.allowed(event)
                    if self.channel != selected_channel:
                        raise ValueError(
                            "The selected channel changed. Reopen its appearance menu."
                        )
                    if picker.values[0] == "__custom":
                        await event.response.send_modal(Edit(self, "lettercolor"))
                        return
                    await event.response.defer(ephemeral=True)
                    values = {key: picker.values[0]}
                    if key == "text_finish":
                        values["text_color"] = None
                    await self.save(event, revision, **values)
                    await self.show(event, page, "Finish saved for this channel only.")

                panel.add_item(
                    discord.ui.TextDisplay(
                        "**" + ("ARTWORK FINISH" if page == "artwork" else "TEXT FINISH") + "**"
                    )
                )
                picker.callback = choose_finish
                panel.add_item(discord.ui.ActionRow(picker))
            if page == "appearance":
                row(
                    "HEADER & FOOTER DESIGN",
                    "Banner title, standard artwork finish and uploads.",
                    "Manage",
                    self.go("artwork"),
                )
                row(
                    "DECORATIVE TEXT",
                    "Four standard lettering finishes or your own hex color.",
                    "Manage",
                    self.go("lettering"),
                )
                row(
                    "EMBED COLOR & TITLES",
                    f"#{config['color']} · This channel and its logs only.",
                    "Edit",
                    ("style",),
                )
                row(
                    "SUBMISSION PANEL TEXT",
                    "Panel heading, message and submit button label.",
                    "Edit",
                    ("text",),
                )
            elif page == "artwork":
                row("HEADER TITLE", config["header_title"], "Edit", ("header",))
                row(
                    "HEADER ARTWORK",
                    "Your upload" if config["header"] else "Selected standard finish",
                    "Upload",
                    ("art:header",),
                )
                row(
                    "FOOTER ARTWORK",
                    "Your upload" if config["footer"] else "Selected standard finish",
                    "Upload",
                    ("art:footer",),
                )

                async def reset(event):
                    await self.save(event, self.revision, header=None, footer=None)
                    await self.show(event, "artwork", "Standard artwork restored for this channel.")

                row(
                    "USE STANDARD ARTWORK",
                    "Restore the selected finish without changing other channels.",
                    "Restore",
                    reset,
                )
            else:
                row(
                    "CUSTOM TEXT COLOR",
                    config["text_color"] or "Following the selected lettering finish.",
                    "Edit color",
                    ("lettercolor",),
                )
                row(
                    "CONFESSION & FOOTER TEXT",
                    "Edit the confession heading, footer text and embed accent.",
                    "Edit",
                    ("style",),
                )
                row(
                    "PANEL HEADING & MESSAGE",
                    "Edit the special question heading and supporting text.",
                    "Edit",
                    ("text",),
                )

            async def preview(event):
                view = Card(
                    config,
                    config["panel_title"],
                    config["panel_text"],
                    detail="Private preview · No submission will be sent.",
                    show_footer_text=config["footer_text"] != "KY BOT · Confessions",
                    buttons=[discord.ui.Button(label=config["submit_label"], disabled=True)],
                )
                files = uploads(config, view)
                try:
                    await event.followup.send(view=view, files=files, ephemeral=True)
                finally:
                    for file in files:
                        file.close()

            row(
                "PREVIEW",
                "Check this channel’s title, artwork and lettering privately.",
                "Preview",
                preview,
            )
        elif page == "permissions":
            for key, label in (
                ("allowed_roles", "Allowed roles (empty = everyone)"),
                ("blocked_roles", "Blocked roles (always takes priority)"),
            ):
                panel.add_item(discord.ui.TextDisplay("**" + label.upper() + "**"))
                picker = discord.ui.RoleSelect(
                    placeholder=label,
                    min_values=0,
                    max_values=25,
                    default_values=[discord.Object(id=r) for r in config[key]],
                )
                revision = self.revision

                async def choose(event, key=key, picker=picker, revision=revision):
                    await event.response.defer(ephemeral=True)
                    await self.save(event, revision, **{key: [r.id for r in picker.values]})
                    await self.show(event, "permissions")

                picker.callback = choose
                panel.add_item(discord.ui.ActionRow(picker))
        elif page in {"queue", "reports"}:
            panel.add_item(discord.ui.TextDisplay("**QUEUE CONTENT**"))
            if page == "queue":
                posts = await self.cog.store.pending(self.guild, self.channel)
            else:
                async with self.cog.store.lock:
                    records = await self.cog.store.execute(
                        (
                            "SELECT DISTINCT post FROM confession_reports WHERE guild=? AND "
                            "state='open'"
                        ),
                        (self.guild,),
                        many=True,
                    )
                posts = []
                for record in records:
                    post = await self.cog.store.post(record[0])
                    if post and post["channel"] == self.channel:
                        posts.append(post)
            if not posts:
                panel.add_item(
                    discord.ui.TextDisplay(
                        "No pending approvals." if page == "queue" else "No open reports."
                    )
                )
            else:
                picker = discord.ui.Select(
                    placeholder="Choose a submission to review",
                    options=[
                        discord.SelectOption(
                            label="Confession " + str(p["data"]["number"]),
                            value=p["id"],
                            description=p["body"][:90],
                        )
                        for p in posts[:25]
                    ],
                )

                async def choose(event):
                    await self.allowed(event)
                    post = await self.cog.store.post(picker.values[0])
                    if not post or post["guild"] != self.guild:
                        raise ValueError("Submission unavailable.")
                    actions = (
                        ("approve", "reject", "ban")
                        if page == "queue"
                        else ("remove", "ban", "dismiss")
                    )
                    view = post_card(
                        config,
                        post,
                        staff=True,
                        buttons=[
                            Action(post["id"], a) for a in actions if a != "ban" or config["bans"]
                        ],
                    )
                    if page == "reports":
                        async with self.cog.store.lock:
                            reports = await self.cog.store.execute(
                                "SELECT reporter,reason FROM confession_reports "
                                "WHERE post=? AND state='open' LIMIT 3",
                                (post["id"],),
                                many=True,
                            )
                        view.children[0].add_item(
                            discord.ui.TextDisplay(
                                "**REPORT DETAILS**\n"
                                + "\n".join(
                                    f"<@{member}> · {safe(reason)[:300]}"
                                    for member, reason in reports
                                )
                            )
                        )
                    files = uploads(config, view)
                    try:
                        await event.response.send_message(
                            view=view,
                            files=files,
                            ephemeral=True,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                    finally:
                        for file in files:
                            file.close()

                picker.callback = choose
                panel.add_item(discord.ui.ActionRow(picker))
                panel.add_item(
                    discord.ui.TextDisplay(
                        "-# Up to 25 submissions. Reload the list after review to see more."
                    )
                )
        elif page == "bans":
            row(
                "CONFESSION BAN ENFORCEMENT",
                "On" if config["bans"] else "Off for this channel · Existing bans are retained.",
                "Turn off" if config["bans"] else "Turn on",
                toggle("bans"),
            )
            row(
                "BAN A MEMBER",
                "Restrict a known member from all confession channels with enforcement on.",
                "Ban member",
                ("ban",),
            )
            row(
                "LIFT A BAN", "Allow the member to confess and reply again.", "Lift ban", ("unban",)
            )

        async def home(event):
            if self.parent and getattr(self.parent, "back_menu", None) is not None:
                await self.parent.back_menu.return_to(event)
            elif self.parent and self.parent.panel_manager:
                await self.parent.panel_manager.reset(self.guild)
            elif self.parent:
                self.parent.section = "overview"
                await self.parent.refresh(event)
            else:
                await self.show(event, "channels")

        back = (
            "appearance"
            if page in {"artwork", "lettering"}
            else "channels"
            if page == "channel"
            else "behavior"
            if page == "permissions"
            else "channel"
            if page in {"appearance", "logging", "behavior"}
            else "logging"
            if page in {"queue", "reports", "bans"}
            else "channels"
        )

        async def close(event):
            closed = discord.ui.LayoutView()
            closed.add_item(
                discord.ui.TextDisplay(
                    "Review closed."
                    if page in {"queue", "reports"}
                    else "Confessions settings closed."
                )
            )
            await event.edit_original_response(content=None, embeds=[], view=closed, attachments=[])
            closed.stop()
            self.stop()

        if page in {"queue", "reports"}:
            panel.add_item(
                discord.ui.Section(
                    "**CLOSE REVIEW**\nClose this private review; keep the shared panel unchanged.",
                    accessory=self.button("Close", close),
                )
            )
        else:
            entry_page = "logging" if self.start_logging and self.channel else "channels"
            entry_back = (
                self.parent is not None and getattr(self.parent, "back_menu", None) is not None
            )
            if entry_back and page in {"channels", entry_page}:
                panel.add_item(
                    discord.ui.Section(
                        "**BACK**\nReturn to Logs.", accessory=self.button("Back", home)
                    )
                )
            elif page != "channels":
                panel.add_item(
                    discord.ui.Section(
                        "**BACK**\nReturn to the previous Confessions menu.",
                        accessory=self.button("Back", self.go(back)),
                    )
                )
            if self.parent:
                panel.add_item(
                    discord.ui.Section(
                        "**COMMAND CENTER**\nReturn to the main menu.",
                        accessory=self.button("Home", home),
                    )
                )
            elif page == "channels":
                panel.add_item(
                    discord.ui.Section(
                        "**CLOSE SETTINGS**\nYour settings are saved.",
                        accessory=self.button("Close", close),
                    )
                )
        panel.add_item(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://confession_footer.png"))
        )
        arrange(panel, page)
        for child in list(panel.children):
            if isinstance(child, discord.ui.TextDisplay) and child.content == "**QUEUE CONTENT**":
                panel.remove_item(child)
        if page in {"queue", "reports"}:
            # Live lists need an explicit reload, kept beside the private introduction.
            children = list(panel.children)
            for index, child in enumerate(children):
                if isinstance(child, discord.ui.TextDisplay) and child.content.startswith(
                    ("Administrators only", "Review member reports")
                ):
                    children[index] = discord.ui.Section(
                        child.content, accessory=self.button("Reload list", self.go(page))
                    )
                    break
            for child in list(panel.children):
                panel.remove_item(child)
            for child in children:
                panel.add_item(child)
        section_dividers(panel, navigation_on_right=True)
        self.add_item(panel)
        files = uploads(menu_style, self) + attachments(page, menu_style)
        try:
            if self.new_private_message:
                self.message = await i.followup.send(
                    view=self,
                    files=files,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                    wait=True,
                )
                self.new_private_message = False
            else:
                self.message = await display_menu(i, self, files, self.parent)
        finally:
            for file in files:
                file.close()
