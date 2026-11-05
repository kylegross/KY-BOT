"""Private custom-embed editor and saved-post library in the Command Center."""

import asyncio
import io
from importlib.resources import files

import discord

from ky_bot.errors import report_error, send_private
from ky_bot.views.sections import section_dividers

from .model import PRESETS, STYLES, color, image_upload, styled, validate


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def editor_artwork():
    return [
        discord.File(
            io.BytesIO(files("ky_bot.design").joinpath("assets", folder, name).read_bytes()),
            filename=name,
        )
        for folder, name in (
            ("header", "ky_header_create_embed.png"),
            ("footer", "ky_footer_create_embed.png"),
        )
    ]


async def editor_display(i, view):
    attachments = editor_artwork()
    try:
        return await i.edit_original_response(
            content=None,
            embeds=[],
            attachments=attachments,
            view=view,
            allowed_mentions=discord.AllowedMentions.none(),
        )
    finally:
        for attachment in attachments:
            attachment.close()


def button(label, callback, *, danger=False):
    item = discord.ui.Button(
        label=label, style=discord.ButtonStyle.danger if danger else discord.ButtonStyle.secondary
    )
    item.callback = callback
    return item


class Base(discord.ui.LayoutView):
    def __init__(self, owner, guild, *, timeout=600):
        super().__init__(timeout=timeout)
        self.owner, self.guild = owner, guild
        self.panel = discord.ui.Container(accent_colour=0xBDC3CA)
        self.panel.add_item(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem("attachment://ky_header_create_embed.png")
            )
        )
        self.add_item(self.panel)

    def text(self, text):
        self.panel.add_item(discord.ui.TextDisplay(text))

    def row(self, text, control):
        self.panel.add_item(discord.ui.Section(text, accessory=control))

    def finish(self):
        section_dividers(self.panel)
        self.panel.add_item(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem("attachment://ky_footer_create_embed.png")
            )
        )

    async def interaction_check(self, i):
        if i.guild_id == self.guild and i.user.id == self.owner and i.permissions.administrator:
            return True
        await send_private(i, "Open Create Embed as a server administrator in this server.")
        return False

    async def on_error(self, i, error, item):
        if isinstance(error, ValueError):
            await send_private(i, str(error))
        elif isinstance(error, discord.HTTPException):
            await send_private(
                i,
                "Discord could not finish that update. Check channel access "
                "and refresh the editor before trying again.",
            )
        else:
            await report_error(i, error)


class Library(Base):
    def __init__(self, cog, owner, guild, rows, *, parent=None, page=0, notice=None):
        super().__init__(owner, guild, timeout=None if parent and parent.panel_manager else 600)
        self.parent = parent
        if notice:
            self.text(notice)
        page = min(max(0, page), max(0, (len(rows) - 1) // 25))
        self.text(
            "**SAVED EMBEDS**\nCreate a custom channel post or open a saved embed. "
            "Editing and previews open privately; publishing is a separate action."
        )

        async def create(i):
            await i.response.defer(ephemeral=True, thinking=True)
            ident = await cog.store.create(guild)
            await Session(cog, owner, guild, ident).show(i)

        self.row(
            "**CREATE EMBED**\nImages, formatted text, custom color and optional sticky delivery.",
            button("Create new", create),
        )
        if rows:
            select = discord.ui.Select(
                placeholder="Choose a saved embed",
                options=[
                    discord.SelectOption(
                        value=ident,
                        label=name[:100],
                        description=(
                            "Removing message…"
                            if deleted
                            else f"Published · channel {channel}"
                            if message
                            else "Saved draft"
                        ),
                    )
                    for ident, name, channel, message, deleted in rows[page * 25 : (page + 1) * 25]
                ],
            )

            async def choose(i):
                await i.response.defer(ephemeral=True, thinking=True)
                await Session(cog, owner, guild, select.values[0]).show(i)

            select.callback = choose
            self.panel.add_item(discord.ui.ActionRow(select))
        else:
            self.text("No saved embeds yet.")

        async def reload(i, offset=0):
            await i.response.defer()
            view = Library(
                cog, owner, guild, await cog.store.listing(guild), parent=parent, page=page + offset
            )
            if parent and parent.panel_manager:
                attachments = editor_artwork()
                try:
                    await parent.panel_manager.edit(parent, i, view, attachments)
                finally:
                    for attachment in attachments:
                        attachment.close()
            else:
                await editor_display(i, view)
            self.stop()

        if len(rows) > 25:
            previous = button("Previous", lambda i: reload(i, -1))
            previous.disabled = page == 0
            following = button("Next", lambda i: reload(i, 1))
            following.disabled = (page + 1) * 25 >= len(rows)
            self.panel.add_item(discord.ui.ActionRow(previous, following))
        self.row(
            "**SAVED EMBEDS**\nRefresh after creating or publishing an embed.",
            button("Refresh", reload),
        )

        async def home(i):
            await i.response.defer()
            if parent and parent.panel_manager:
                await parent.panel_manager.reset(guild)
            elif parent:
                parent.section = "overview"
                await parent.refresh(i)
            else:
                closed = discord.ui.LayoutView()
                closed.add_item(
                    discord.ui.TextDisplay("Embed editor closed. Your drafts are saved.")
                )
                await i.edit_original_response(view=closed, attachments=[])
                closed.stop()
            self.stop()

        self.row(
            "**COMMAND CENTER**\nReturn to the main menu."
            if parent
            else "**PRIVATE EDITOR**\nYour drafts stay saved when you close this menu.",
            button("Home" if parent else "Close", home),
        )
        self.finish()

    async def interaction_check(self, i):
        if self.parent:
            return await self.parent.interaction_check(i)
        return await super().interaction_check(i)


class Session:
    def __init__(self, cog, owner, guild, ident):
        self.cog, self.owner, self.guild, self.ident = cog, owner, guild, ident
        self.lock = asyncio.Lock()
        self.row = None
        self.page = "main"
        self.style = "bold"
        self.view = None

    async def allowed(self, i):
        if i.user.id == self.owner and i.guild_id == self.guild and i.permissions.administrator:
            return True
        await send_private(i, "Only the administrator who opened this private editor can use it.")
        return False

    async def save(self, i, revision, **changes):
        if not await self.allowed(i):
            return
        async with self.lock:
            async with self.cog.lock(self.ident):
                await self.cog.store.save(self.guild, self.ident, revision, **changes)
        await self.show(
            i, self.page, "Draft saved. Publish when you’re ready to update the channel."
        )

    async def show(self, i, page="main", notice=None):
        self.row = await self.cog.store.get(self.guild, self.ident)
        self.page = page
        view = Editor(self, notice)
        await editor_display(i, view)
        if self.view:
            self.view.stop()
        self.view = view


class EditModal(discord.ui.Modal):
    def __init__(self, session, mode):
        super().__init__(
            title={
                "content": "Embed content",
                "color": "Custom highlight color",
                "count": "Sticky message count",
                "format": "Add formatted text",
                "header": "Upload header image",
                "footer": "Upload footer image",
            }[mode]
        )
        self.session, self.mode = session, mode
        self.revision = session.row["revision"]
        self.style = session.style
        draft = session.row["draft"]
        self.fields = {}
        self.upload = None
        if mode in {"header", "footer"}:
            self.upload = discord.ui.FileUpload(max_values=1, required=True)
            self.add_item(
                discord.ui.Label(text="Choose an image (up to 8 MB)", component=self.upload)
            )
            return
        specs = {
            "content": [
                ("name", "Saved name (only administrators see this)", draft["name"], 64, True),
                ("title", "Title (optional)", draft["title"], 200, False),
                ("body", "Message · Discord formatting supported", draft["body"], 3500, False),
                ("footer_text", "Footer text (optional)", draft["footer_text"], 200, False),
            ],
            "color": [("color", "Highlight color · #RRGGBB", draft["color"], 7, True)],
            "count": [
                (
                    "sticky_every",
                    "Repost after this many messages (1–10,000)",
                    str(draft["sticky_every"] or 1),
                    5,
                    True,
                )
            ],
            "format": [("text", "Text to format and append to your message", "", 3000, True)],
        }[mode]
        for key, label, value, limit, required in specs:
            field = discord.ui.TextInput(
                label=label,
                default=value,
                max_length=limit,
                required=required,
                style=discord.TextStyle.paragraph
                if key in {"body", "text"}
                else discord.TextStyle.short,
            )
            self.fields[key] = field
            self.add_item(field)

    async def on_submit(self, i):
        s = self.session
        if not await s.allowed(i):
            return
        await i.response.defer()
        try:
            if self.upload is not None:
                attachment = self.upload.values[0]
                if attachment.size > 8 * 1024 * 1024:
                    raise ValueError("Upload an image under 8 MB.")
                encoded = await asyncio.to_thread(image_upload, await attachment.read())
                changes = {self.mode: encoded}
            else:
                changes = {key: field.value for key, field in self.fields.items()}
                if self.mode == "color":
                    changes["color"] = color(changes["color"])
                elif self.mode == "count":
                    try:
                        count = int(changes["sticky_every"])
                    except ValueError:
                        raise ValueError("Enter a whole message count from 1 to 10,000.") from None
                    if not 1 <= count <= 10000:
                        raise ValueError("Enter a message count from 1 to 10,000.")
                    changes["sticky_every"] = count
                elif self.mode == "format":
                    addition = styled(changes["text"], self.style)
                    old = s.row["draft"]["body"]
                    changes = dict(body=old + ("\n\n" if old else "") + addition)
            await s.save(i, self.revision, **changes)
        except ValueError as error:
            await send_private(i, str(error))

    async def on_error(self, i, error):
        await report_error(i, error)


class Editor(Base):
    def __init__(self, s, notice=None):
        super().__init__(s.owner, s.guild)
        self.session = s
        row, data, page = s.row, s.row["draft"], s.page
        revision = row["revision"]
        self.text(
            f"**{safe(data['name'])}**\nPrivate draft · Changes go live only when you publish."
        )
        if notice:
            self.text(notice)

        def go(page):
            async def navigate(i):
                await i.response.defer()
                await s.show(i, page)

            return navigate

        def modal(mode):
            async def open_modal(i):
                await i.response.send_modal(EditModal(s, mode))

            return open_modal

        if row["deleted"]:
            self.text(
                "This embed is queued for deletion. KY BOT will retry removing its "
                "message once it can access the channel."
            )
        elif page == "main":
            for heading, description, destination in (
                ("CONTENT", "Title, message, footer text and saved name.", "content"),
                ("IMAGES", "Optional header and footer images.", "images"),
                (
                    "COLOR & FORMATTING",
                    "Highlight color, special lettering and text styles.",
                    "style",
                ),
                ("CHANNEL & STICKY", "Choose where to post and how often to move it.", "delivery"),
                (
                    "PREVIEW & PUBLISH",
                    "Review privately, publish updates or delete the embed.",
                    "publish",
                ),
            ):
                self.row(f"**{heading}**\n-# {description}", button("Manage", go(destination)))
            self.text(
                "-# "
                + (
                    "Published message exists. Editing keeps it unchanged until Publish."
                    if row["message"]
                    else "Saved draft · Not published yet."
                )
            )
        elif page == "content":
            summary = safe(data["title"] or "No title yet")
            self.row(
                f"**CONTENT**\n{summary}\n-# Message: {len(data['body'])}/3500 characters.",
                button("Edit text", modal("content")),
            )
            self.text(
                "Use Discord formatting directly: **bold**, *italic*, __underline__, "
                "lists, headings, quotes and links. Decorative symbols and Unicode "
                "lettering can be pasted into any text field. Role and @everyone pings "
                "are suppressed when the post is sent."
            )
        elif page == "images":
            for key, title in (("header", "HEADER IMAGE"), ("footer", "FOOTER IMAGE")):
                self.row(
                    f"**{title}**\n-# " + ("Image saved" if data[key] else "No image"),
                    button("Upload image", modal(key)),
                )
                if data[key]:

                    async def remove(i, key=key):
                        await i.response.defer()
                        await s.save(i, revision, **{key: None})

                    self.row(
                        f"**REMOVE {title}**\n-# Clear it from this draft.",
                        button("Remove", remove),
                    )
            self.text(
                "-# Images are saved with the embed so sticky reposts keep working "
                "after restarts. Animated images are converted to a static PNG."
            )
        elif page == "style":
            self.row(
                f"**HIGHLIGHT COLOR**\n#{data['color']}", button("Custom color", modal("color"))
            )
            palette = discord.ui.Select(
                placeholder="Choose a highlight color",
                options=[
                    discord.SelectOption(label=label, value=value, default=value == data["color"])
                    for value, label in PRESETS.items()
                ],
            )

            async def choose_color(i):
                await i.response.defer()
                await s.save(i, revision, color=palette.values[0])

            palette.callback = choose_color
            self.panel.add_item(discord.ui.ActionRow(palette))
            self.panel.add_item(discord.ui.Separator())
            self.text(
                "**FORMATTING & SPECIAL TEXT**\nChoose a style, then add text to your message."
            )
            select = discord.ui.Select(
                placeholder="Choose a text style",
                options=[
                    discord.SelectOption(label=label, value=key, default=key == s.style)
                    for key, label in STYLES.items()
                ],
            )

            async def style(i):
                s.style = select.values[0]
                await i.response.defer()

            select.callback = style
            self.panel.add_item(discord.ui.ActionRow(select))
            self.row(
                "**ADD STYLED TEXT**\n-# Appends formatted text to the message.",
                button("Add text", modal("format")),
            )
        elif page in {"delivery", "channel"}:
            self.row(
                "**DESTINATION CHANNEL**\n"
                + (f"<#{data['channel']}>" if data["channel"] else "Not configured"),
                button("Choose channel", go("channel")),
            )
            if page == "channel":
                select = discord.ui.ChannelSelect(
                    channel_types=[discord.ChannelType.text],
                    placeholder="Choose destination channel",
                )

                async def choose(i):
                    await i.response.defer()
                    channel = await s.cog.channel(s.guild, select.values[0].id)
                    await s.save(i, revision, channel=channel.id)

                select.callback = choose
                self.panel.add_item(discord.ui.ActionRow(select))
                self.panel.add_item(discord.ui.Separator())
            every = data["sticky_every"]

            async def toggle(i):
                await i.response.defer()
                await s.save(i, revision, sticky_every=0 if every else 1)

            self.row(
                "**STICKY MESSAGE**\n"
                + (
                    "Off"
                    if not every
                    else "Repost after every message"
                    if every == 1
                    else f"Repost after {every} messages"
                ),
                button("Disable" if every else "Enable", toggle),
            )
            self.row(
                "**MESSAGE COUNT**\n-# 1 means every new message. Choose a larger number to wait.",
                button("Set count", modal("count")),
            )
            self.text(
                "-# Each move deletes the old copy. KY BOT’s own posts do not count, "
                "so multiple sticky menus do not keep moving each other. "
                "Publish to apply these delivery settings."
            )
        elif page == "publish":

            async def preview(i):
                await i.response.defer()
                draft = validate(data, publish=False)
                # A preview needs content but not a destination channel.
                validate(draft | dict(channel=draft["channel"] or 1), publish=True)
                await s.cog.post(i.followup, draft, private=True)

            async def publish(i):
                await i.response.defer()
                result = await s.cog.publish(i, s.ident, revision)
                await s.show(i, "publish", result)

            async def delete(i):
                await i.response.defer()
                await editor_display(i, DeleteConfirm(s, revision))

            self.row(
                "**PRIVATE PREVIEW**\nSee exactly how the post will look.",
                button("Preview", preview),
            )
            self.row(
                "**PUBLISH / UPDATE**\nPost to the selected channel or update the existing copy.",
                button("Publish", publish),
            )
            self.row(
                "**DELETE EMBED**\nRemove the saved embed and its posted message.",
                button("Delete", delete, danger=True),
            )
        if page != "main" and not row["deleted"]:
            self.row("**EMBED EDITOR**\n-# Return to editing options.", button("Back", go("main")))

        async def library(i):
            await i.response.defer()
            await editor_display(
                i, Library(s.cog, s.owner, s.guild, await s.cog.store.listing(s.guild))
            )
            self.stop()

        self.row(
            "**SAVED EMBEDS**\n-# Your drafts are saved automatically.",
            button("All embeds", library),
        )
        self.finish()

    async def interaction_check(self, i):
        return await self.session.allowed(i)


class DeleteConfirm(Base):
    def __init__(self, s, revision):
        super().__init__(s.owner, s.guild)
        self.session, self.revision = s, revision
        self.used = False
        self.text(
            "**DELETE EMBED**\nRemove the saved draft and its published copy. "
            "Sticky reposting stops. The channel and other messages are kept. "
            "This cannot be undone."
        )

        async def confirm(i):
            if not await s.allowed(i):
                return
            await i.response.defer()
            async with s.lock:
                if self.used:
                    return await send_private(i, "This confirmation has already been handled.")
                await s.cog.authorize(i)
                async with s.cog.lock(s.ident):
                    await s.cog.store.delete(s.guild, s.ident, revision)
                    self.used = True
                    removed = await s.cog.clean(s.guild, s.ident)
            view = Library(
                s.cog,
                s.owner,
                s.guild,
                await s.cog.store.listing(s.guild),
                notice="Embed deleted."
                if removed
                else "Removal queued. Check KY BOT’s access to the old channel; it will retry.",
            )
            await editor_display(i, view)
            self.stop()

        async def cancel(i):
            await i.response.defer()
            async with s.lock:
                if self.used:
                    return await send_private(i, "This confirmation has already been handled.")
                self.used = True
            await s.show(i, "publish", "Embed kept.")
            self.stop()

        self.row(
            "**CONFIRM DELETION**\nPermanently remove this embed.",
            button("Delete embed", confirm, danger=True),
        )
        self.row("**KEEP EMBED**\nReturn without deleting anything.", button("Cancel", cancel))
        self.finish()

    async def interaction_check(self, i):
        return await self.session.allowed(i)
