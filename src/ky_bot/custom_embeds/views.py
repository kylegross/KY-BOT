"""Custom-embed editing in the Command Center, with explicit private previews."""

import asyncio
import io
from importlib.resources import files

import discord

from ky_bot.errors import report_error, send_private
from ky_bot.views.menu_context import defer_menu, display_menu, menu_parent
from ky_bot.views.menu_strips import group_sections, strip_files
from ky_bot.views.sections import section_dividers

from .model import (
    FIELD_STYLES,
    MAX_SECTIONS,
    PRESETS,
    color,
    defaults,
    image_upload,
    styled,
    validate,
)


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def _base_editor_artwork(mode=None):
    header_name = "ky_header_manage_embeds" + ("_" + mode if mode else "") + ".png"
    return [
        discord.File(
            io.BytesIO(
                files("ky_bot.design")
                .joinpath("assets", folder, header_name if folder == "header" else name)
                .read_bytes()
            ),
            filename=name,
        )
        for folder, name in (
            ("header", "ky_header_create_embed.png"),
            ("footer", "ky_footer_create_embed.png"),
        )
    ]


async def editor_display(i, view):
    attachments = editor_artwork(view)
    try:
        session = getattr(view, "session", None)
        parent = getattr(view, "parent", getattr(session, "parent", None))
        parent = menu_parent(parent, i)
        if session is not None:
            session.parent = parent
        elif hasattr(view, "parent"):
            view.parent = parent
        return await display_menu(i, view, attachments, parent)
    finally:
        for attachment in attachments:
            attachment.close()


def button(label, callback, *, danger=False):
    item = discord.ui.Button(
        label=label, style=discord.ButtonStyle.danger if danger else discord.ButtonStyle.secondary
    )
    item.callback = callback
    return item


def initial_channel(interaction):
    channel = getattr(interaction, "channel", None)
    if isinstance(channel, discord.Thread):
        channel = channel.parent
    return channel.id if isinstance(channel, discord.TextChannel) else 0


class Base(discord.ui.LayoutView):
    def __init__(self, owner, guild, *, timeout=600):
        super().__init__(timeout=timeout)
        self.owner, self.guild = owner, guild
        self.strip_menu = "custom_embeds:library"
        self.panel = discord.ui.Container(accent_colour=0xD6E2F3)
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
        group_sections(self.panel, self.strip_menu)
        children = list(self.panel.children)
        for index, child in enumerate(children):
            if (
                isinstance(child, discord.ui.Section)
                and child.children[0].content.startswith("**DELETE EMBED**")
                and index > 0
                and isinstance(children[index - 1], discord.ui.ActionRow)
                and any(
                    isinstance(item, discord.ui.Select) for item in children[index - 1].children
                )
            ):
                children.insert(
                    index,
                    discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.large),
                )
                break
        for child in list(self.panel.children):
            self.panel.remove_item(child)
        for child in children:
            self.panel.add_item(child)
        section_dividers(self.panel, navigation_on_right=True)
        self.panel.add_item(
            discord.ui.MediaGallery(
                discord.MediaGalleryItem("attachment://ky_footer_create_embed.png")
            )
        )

    async def interaction_check(self, i):
        if i.guild_id == self.guild and i.user.id == self.owner and i.permissions.administrator:
            return True
        await send_private(i, "Open Manage Embeds as a server administrator in this server.")
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
    def __init__(
        self, cog, owner, guild, rows, *, parent=None, page=0, notice=None, deleting=False
    ):
        super().__init__(owner, guild, timeout=None if parent and parent.panel_manager else 600)
        self.parent = parent
        if notice:
            self.text(notice)
        page = min(max(0, page), max(0, (len(rows) - 1) // 25))
        self.text(
            "Choose the saved embed you want to delete. You will confirm before it is removed."
            if deleting
            else "Create a custom channel post or choose a saved embed to edit or delete. "
            "Edit here; previews open privately. Publishing is a separate action."
        )

        async def create(i):
            await defer_menu(i, menu_parent(self.parent, i))
            ident = await cog.store.create(guild, initial_channel(i))
            await Session(
                cog, owner, guild, ident, creating=True, parent=menu_parent(self.parent, i)
            ).show(i)

        self.row(
            "**CREATE EMBED**\nImages, formatted text, custom color and optional sticky delivery.",
            button("Create new", create),
        )

        async def reload(i, offset=0):
            parent = menu_parent(self.parent, i)
            await i.response.defer()
            view = Library(
                cog,
                owner,
                guild,
                await cog.store.listing(guild),
                parent=parent,
                page=page + offset,
                deleting=deleting,
            )
            if parent and parent.panel_manager:
                attachments = editor_artwork(view)
                try:
                    await parent.panel_manager.edit(parent, i, view, attachments)
                finally:
                    for attachment in attachments:
                        attachment.close()
            else:
                await editor_display(i, view)
            self.stop()

        self.row(
            "**SAVED EMBEDS**\nRefresh after creating or publishing an embed.",
            button("Refresh", reload),
        )
        if rows:
            select = discord.ui.Select(
                placeholder="Choose an embed to delete"
                if deleting
                else "Choose a saved embed to edit or delete",
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
                await defer_menu(i, menu_parent(self.parent, i))
                session = Session(
                    cog, owner, guild, select.values[0], parent=menu_parent(self.parent, i)
                )
                session.row = await cog.store.get(guild, session.ident)
                if session.row["deleted"]:
                    await session.show(i)
                else:
                    await editor_display(
                        i,
                        DeleteConfirm(session, session.row["revision"])
                        if deleting
                        else SavedEmbed(session),
                    )

            select.callback = choose
            self.panel.add_item(discord.ui.ActionRow(select))
        else:
            self.text("No saved embeds yet.")

        async def choose_delete(i):
            await defer_menu(i, menu_parent(self.parent, i))
            await editor_display(
                i,
                Library(
                    cog,
                    owner,
                    guild,
                    await cog.store.listing(guild),
                    parent=menu_parent(self.parent, i),
                    deleting=True,
                ),
            )

        if rows and not deleting:
            self.row(
                "**DELETE EMBED**\nChoose a saved embed to remove, then confirm.",
                button("Delete embed", choose_delete),
            )

        if len(rows) > 25:
            previous = button("Previous", lambda i: reload(i, -1))
            previous.disabled = page == 0
            following = button("Next", lambda i: reload(i, 1))
            following.disabled = (page + 1) * 25 >= len(rows)
            self.panel.add_item(discord.ui.ActionRow(previous, following))

        async def home(i):
            parent = menu_parent(self.parent, i)
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
        self.parent = menu_parent(self.parent, i)
        if self.parent:
            return await self.parent.interaction_check(i)
        return await super().interaction_check(i)


class SavedEmbed(Base):
    """An explicit action menu for the embed selected in Manage Embeds."""

    def __init__(self, session):
        super().__init__(session.owner, session.guild)
        self.session = session
        self.header_mode = "edit"
        self.strip_menu = "custom_embeds:selected"
        self.text("**SELECTED EMBED**\n" + safe(session.row["draft"]["name"]) + "\n-# \u200b")

        async def edit(i):
            await i.response.defer()
            await session.show(i, "main")
            self.stop()

        async def delete(i):
            await i.response.defer()
            await editor_display(i, DeleteConfirm(session, session.row["revision"]))
            self.stop()

        async def back(i):
            await i.response.defer()
            await editor_display(
                i,
                Library(
                    session.cog,
                    session.owner,
                    session.guild,
                    await session.cog.store.listing(session.guild),
                    parent=session.parent,
                ),
            )
            self.stop()

        self.row(
            "**EDIT EMBED**\nChange the content, images, formatting or delivery settings.",
            button("Edit embed", edit),
        )
        self.row(
            "**DELETE EMBED**\nRemove this draft and its published copy.",
            button("Delete embed", delete),
        )
        self.row("**SAVED EMBEDS**\nReturn to the saved embed list.", button("All embeds", back))
        self.finish()

    async def interaction_check(self, i):
        return await self.session.allowed(i)


class Session:
    def __init__(self, cog, owner, guild, ident, *, creating=False, parent=None):
        self.cog, self.owner, self.guild, self.ident = cog, owner, guild, ident
        self.lock = asyncio.Lock()
        self.row = None
        self.creating = creating
        self.parent = parent
        self.page = "main"
        self.style = "bold"
        self.section_index = 0
        self.view = None

    async def allowed(self, i):
        self.parent = menu_parent(self.parent, i)
        if self.parent is not None and not await self.parent.interaction_check(i):
            return False
        if i.user.id == self.owner and i.guild_id == self.guild and i.permissions.administrator:
            return True
        await send_private(i, "Only the administrator using this editor can use it.")
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
        self.row["draft"] = defaults() | self.row["draft"]
        if self.row["published"]:
            self.creating = False
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
                "title": "Edit title",
                "header_title": "Header image title",
                "body": "Edit message",
                "footer_text": "Edit footer text",
                "name": "Saved draft name",
                "color": "Custom highlight color",
                "text_color": "Decorative text color",
                "section_add": "Add section",
                "section_edit": "Edit section",
                "count": "Sticky message count",
                "format": "Add styled text to message",
                "header": "Upload header image",
                "footer": "Upload footer image",
            }[mode]
        )
        self.session, self.mode = session, mode
        self.revision = session.row["revision"]
        self.section_index = session.section_index
        self.style = session.style
        draft = session.row["draft"]
        sections = draft.get("sections", [])
        section = sections[min(self.section_index, len(sections) - 1)] if sections else {}
        self.fields = {}
        self.upload = None
        if mode in {"header", "footer"}:
            self.upload = discord.ui.FileUpload(max_values=1, required=True)
            self.add_item(
                discord.ui.Label(text="Choose an image (up to 8 MB)", component=self.upload)
            )
            return
        field_specs = {
            "title": ("Title · large heading by default", 200),
            "header_title": ("Title inside the header image (optional)", 80),
            "body": ("Message · Discord formatting supported", 3500),
            "footer_text": ("Footer text", 200),
            "name": ("Saved name (only administrators see this)", 64),
        }
        specs = {
            **{
                key: [(key, label, draft[key], limit, key == "name")]
                for key, (label, limit) in field_specs.items()
            },
            "content": [
                ("name", "Saved name (only administrators see this)", draft["name"], 64, True),
                ("title", "Title (optional)", draft["title"], 200, False),
                ("body", "Message · Discord formatting supported", draft["body"], 3500, False),
                ("footer_text", "Footer text (optional)", draft["footer_text"], 200, False),
            ],
            "text_color": [
                (
                    "text_color",
                    "Text color · #RRGGBB (blank follows finish)",
                    draft.get("text_color") or "",
                    7,
                    False,
                )
            ],
            "section_add": [
                ("heading", "Decorative section heading", "", 80, True),
                ("body", "Section message (optional)", "", 1500, False),
            ],
            "section_edit": [
                ("heading", "Decorative section heading", section.get("heading", ""), 80, True),
                ("body", "Section message (optional)", section.get("body", ""), 1500, False),
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
            "format": [("text", "New text · appended to the message below", "", 3000, True)],
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
                if self.mode in {"section_add", "section_edit"}:
                    sections = [dict(section) for section in s.row["draft"].get("sections", [])]
                    if self.mode == "section_add":
                        sections.append(changes)
                    elif self.section_index < len(sections):
                        sections[self.section_index] = changes
                    else:
                        raise ValueError("This section changed. Reopen the section editor.")
                    changes = dict(sections=sections)
                elif self.mode == "color":
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
        self.header_mode = "create" if s.creating else "edit"
        row, data, page = s.row, s.row["draft"], s.page
        revision = row["revision"]
        self.strip_menu = "custom_embeds" if page == "main" else f"custom_embeds:{page}"
        self.text(f"**{safe(data['name'])}**\nSaved draft · Changes go live only when you publish.")
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
            self.text(
                "**DESTINATION CHANNEL**\nDefaults to the channel where this draft was created."
            )
            self.channel_picker(s, data, revision)
            self.panel.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.large))
            self.row("**DRAFT NAME**\n" + safe(data["name"]), button("Rename", modal("name")))
            for heading, description, destination in (
                ("IMAGES", "Optional header and footer images.", "images"),
                (
                    "CONTENT & TEXT STYLES",
                    "Edit title, message, footer and their text styles.",
                    "content",
                ),
                (
                    "FORMATTING",
                    "Text color, decorative headings and highlight color.",
                    "formatting",
                ),
                ("STICKY DELIVERY", "Choose how often this post moves to the bottom.", "delivery"),
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
            for key, label, style_key in (
                ("title", "TITLE", "title_style"),
                ("body", "MESSAGE", "body_style"),
                ("footer_text", "FOOTER TEXT", "footer_style"),
            ):
                self.text(f"**{label} STYLE**\nApplies to this entire field.")
                style_select = discord.ui.Select(
                    placeholder=f"Choose {label.lower()} style",
                    options=[
                        discord.SelectOption(
                            label=title,
                            value=value,
                            default=value
                            == data.get(style_key, "large" if key == "title" else "plain"),
                        )
                        for value, title in FIELD_STYLES.items()
                    ],
                )

                async def field_style(i, select=style_select, style_key=style_key):
                    await i.response.defer()
                    await s.save(i, revision, **{style_key: select.values[0]})

                style_select.callback = field_style
                self.panel.add_item(discord.ui.ActionRow(style_select))
                value = safe(data[key][:160]) if data[key] else "No text yet"
                self.row(f"**{label}**\n{value}", button("Edit " + label.lower(), modal(key)))
            self.text("-# You can also enter Discord Markdown directly. Mentions never ping.")
            self.row(
                "**SECTIONS**\nAdd, edit or remove section headings and messages.",
                button("Manage sections", go("sections")),
            )
        elif page == "formatting":
            from ky_bot.design.menu_design import FINISHES

            for key, label in (
                ("title_style", "TITLE STYLE"),
                ("footer_style", "FOOTER TEXT STYLE"),
            ):
                self.text(f"**{label}**\nChoose how this text is displayed.")
                picker = discord.ui.Select(
                    placeholder="Choose " + label.lower(),
                    options=[
                        discord.SelectOption(label=label, value=value, default=data[key] == value)
                        for value, label in FIELD_STYLES.items()
                    ],
                )

                async def style_choice(i, picker=picker, key=key):
                    await i.response.defer()
                    await s.save(i, revision, **{key: picker.values[0]})

                picker.callback = style_choice
                self.panel.add_item(discord.ui.ActionRow(picker))

            self.text(
                "**DECORATIVE TEXT FINISH**\nFor decorative titles, footers and section headings."
            )
            finish = discord.ui.Select(
                placeholder="Choose a decorative text finish or custom color",
                options=[
                    discord.SelectOption(
                        label=label,
                        value=key,
                        default=key == data["text_finish"] and not data["text_color"],
                    )
                    for key, label in FINISHES.items()
                ]
                + [
                    discord.SelectOption(
                        label="Custom color…", value="__custom", default=bool(data["text_color"])
                    )
                ],
            )

            async def choose_finish(i):
                if finish.values[0] == "__custom":
                    await i.response.send_modal(EditModal(s, "text_color"))
                    return
                await i.response.defer()
                await s.save(i, revision, text_finish=finish.values[0], text_color=None)

            finish.callback = choose_finish
            self.panel.add_item(discord.ui.ActionRow(finish))
            self.row(
                "**CUSTOM TEXT COLOR**\n" + (data["text_color"] or "Using the selected finish."),
                button("Custom color", modal("text_color")),
            )
            self.row(
                "**HIGHLIGHT COLOR**\nSet the accent beside the message.",
                button("Change color", go("style")),
            )
        elif page == "sections":
            sections = data.get("sections", [])
            self.text(
                f"**SECTIONS**\n{len(sections)} of {MAX_SECTIONS} sections. "
                "Shown below the main message, in the order you add them."
            )
            add = button("+ Add section", modal("section_add"))
            add.disabled = len(sections) >= MAX_SECTIONS
            self.panel.add_item(discord.ui.ActionRow(add))
            if sections:
                index = min(s.section_index, len(sections) - 1)
                s.section_index = index
                select = discord.ui.Select(
                    placeholder="Choose a section to edit",
                    options=[
                        discord.SelectOption(
                            label=f"{n + 1}. {section['heading']}"[:100],
                            value=str(n),
                            default=n == index,
                        )
                        for n, section in enumerate(sections)
                    ],
                )

                async def choose_section(i):
                    s.section_index = int(select.values[0])
                    await i.response.defer()
                    await s.show(i, "sections")

                select.callback = choose_section
                self.panel.add_item(discord.ui.ActionRow(select))
                self.row(
                    "**SELECTED SECTION**\n" + safe(sections[index]["heading"]),
                    button("Edit section", modal("section_edit")),
                )

                async def remove_section(i):
                    await i.response.defer()
                    await s.save(i, revision, sections=sections[:index] + sections[index + 1 :])

                self.row(
                    "**REMOVE SECTION**\nRemove this heading and its message from the draft.",
                    button("Remove section", remove_section),
                )
        elif page == "images":
            for key, title in (("header", "HEADER IMAGE"), ("footer", "FOOTER IMAGE")):
                self.row(
                    f"**{key.upper()} FILE**\n"
                    + (
                        "Image saved. Upload a replacement."
                        if data[key]
                        else "Upload your own image."
                    ),
                    button("Upload image", modal(key)),
                )
                from ky_bot.design.menu_design import FINISHES

                from .artwork import standard_image

                self.text(f"**{key.upper()} FINISH**\nOr choose a standard KY BOT design.")
                picker = discord.ui.Select(
                    placeholder=f"Choose a standard {key}",
                    options=[
                        discord.SelectOption(
                            label=label,
                            value=value,
                            default=data[key] == standard_image(key, value),
                        )
                        for value, label in FINISHES.items()
                    ],
                )

                async def standard(i, key=key, picker=picker):
                    await i.response.defer()
                    await s.save(
                        i,
                        revision,
                        **(
                            {key: standard_image(key, picker.values[0])}
                            | ({"header_finish": picker.values[0]} if key == "header" else {})
                        ),
                    )

                picker.callback = standard
                self.panel.add_item(discord.ui.ActionRow(picker))
                if key == "header":
                    self.row(
                        "**HEADER IMAGE TITLE**\n"
                        + (
                            safe(data.get("header_title", ""))
                            or "No image title yet. Appears inside the header image."
                        ),
                        button("Edit header title", modal("header_title")),
                    )
                if data[key]:

                    async def remove(i, key=key):
                        await i.response.defer()
                        await s.save(i, revision, **{key: None})

                    self.row(
                        f"**REMOVE {title}**\nClear it from this draft.",
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
                button("Delete", delete),
            )
        if page != "main" and not row["deleted"]:
            self.row(
                "**EMBED EDITOR**\n-# Return to editing options.",
                button(
                    "Back",
                    go(
                        {"sections": "content", "style": "formatting", "channel": "delivery"}.get(
                            page, "main"
                        )
                    ),
                ),
            )

        async def library(i):
            await i.response.defer()
            await editor_display(
                i,
                Library(
                    s.cog, s.owner, s.guild, await s.cog.store.listing(s.guild), parent=s.parent
                ),
            )
            self.stop()

        if page == "main":
            self.row(
                "**SAVED EMBEDS**\n-# Your drafts are saved automatically.",
                button("All embeds", library),
            )
        self.finish()

    def channel_picker(self, s, data, revision):
        select = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            placeholder="Choose destination channel",
            default_values=[discord.Object(id=data["channel"])] if data["channel"] else [],
        )

        async def choose(i):
            await i.response.defer()
            channel = await s.cog.channel(s.guild, select.values[0].id)
            await s.save(i, revision, channel=channel.id)

        select.callback = choose
        self.panel.add_item(discord.ui.ActionRow(select))

    async def interaction_check(self, i):
        return await self.session.allowed(i)


class DeleteConfirm(Base):
    def __init__(self, s, revision):
        super().__init__(s.owner, s.guild)
        self.session, self.revision = s, revision
        self.header_mode = "create" if s.creating else "edit"
        self.strip_menu = "custom_embeds:delete"
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
                parent=s.parent,
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
            button("Delete embed", confirm),
        )
        self.row("**KEEP EMBED**\nReturn without deleting anything.", button("Cancel", cancel))
        self.finish()

    async def interaction_check(self, i):
        return await self.session.allowed(i)


def editor_artwork(view=None):
    menu = view.strip_menu if view is not None else "custom_embeds:library"
    return _base_editor_artwork(getattr(view, "header_mode", None)) + strip_files(
        menu, "silver_neon"
    )
