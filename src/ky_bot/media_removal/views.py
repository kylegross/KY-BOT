"""Command Center setup and a reuse of the complete custom-panel appearance editor."""

import asyncio
import io

import discord

from ky_bot.advice.command import administrator
from ky_bot.custom_embeds.model import render
from ky_bot.custom_embeds.model import validate as validate_panel
from ky_bot.custom_embeds.views import Session
from ky_bot.design.menu_design import default_profile, render_menu
from ky_bot.errors import report_error, send_private
from ky_bot.views.media import NoAltMediaGalleryItem
from ky_bot.views.menu_context import display_menu, menu_parent
from ky_bot.views.menu_strips import GROUPS, group_sections, strip_files
from ky_bot.views.sections import section_dividers

GROUPS["media_removal"] = (
    ("CHANNEL & DELIVERY", ("PANEL CHANNEL", "MEDIA REMOVAL STATUS")),
    (
        "DESIGN",
        ("PANEL TEXT & ARTWORK", "TITLE & LINE COLORS", "BUTTON HEADING & TEXT", "BUTTON POSITION"),
    ),
    ("REVIEW & PUBLISH", ("PREVIEW PANEL", "PUBLISH PANEL", "CLEANUP SCOPE")),
)


def render_panel(guild, config, *, preview=False):
    from .command import RemovalAction

    view, attachments = render(config["panel"])
    panel = view.children[0]
    children = list(panel.children)
    position = next(
        (
            n
            for n, child in enumerate(children)
            if isinstance(child, discord.ui.MediaGallery)
            and any(
                item.media.url.endswith("/custom_footer.png")
                or item.media.url.endswith("://custom_footer.png")
                for item in child.items
            )
        ),
        len(children),
    )
    action = RemovalAction(guild, config["button_label"])
    action.item.disabled = preview
    controls = [discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.small)]
    heading = config["button_heading"].strip()
    if heading:
        from ky_bot.checklists.artwork import title_image

        image = title_image(
            heading,
            config["panel"]["text_finish"],
            config["panel"]["text_color"],
            config["panel"]["line_color"],
            shimmer=True,
        )
        attachments.append(
            discord.File(io.BytesIO(image), filename="media_button_heading.png", description="")
        )
        controls.append(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://media_button_heading.png"))
        )
    text = discord.utils.escape_mentions(config["button_text"])
    controls += (
        [discord.ui.Section(text, accessory=action)]
        if config["inline"]
        else [discord.ui.TextDisplay(text), discord.ui.ActionRow(action)]
    )
    for child in list(panel.children):
        panel.remove_item(child)
    for child in children[:position] + controls + children[position:]:
        panel.add_item(child)
    if view.total_children_count > 40 or len(attachments) > 10:
        for attachment in attachments:
            attachment.close()
        view.stop()
        raise ValueError("This panel has too many sections. Remove a section before publishing.")
    return view, attachments


class PanelStore:
    """Expose only this feature's panel draft to the existing appearance editor."""

    def __init__(self, cog, guild):
        self.cog, self.guild = cog, guild

    async def get(self, guild, ident):
        if guild != self.guild or str(ident) != str(self.guild):
            raise ValueError("Open this server’s media removal editor.")
        revision, config = await self.cog.store.config(guild)
        return dict(
            revision=revision,
            draft=config["panel"],
            published=config["panel"] if config["message"] else None,
            deleted=0,
            message=config["message"],
        )

    async def save(self, guild, ident, expected, **changes):
        await self.get(guild, ident)
        revision, config = await self.cog.store.config(guild)
        if expected != revision:
            raise ValueError("Settings changed. Refresh the media removal editor.")
        if "channel" in changes and changes["channel"] != config["channel"]:
            raise ValueError("Change the panel channel in Media Removal setup.")
        if changes.get("sticky_every"):
            raise ValueError("This permanent panel does not use sticky reposting.")
        panel = validate_panel(config["panel"] | changes)
        revision, _ = await self.cog.store.configure(guild, expected, panel=panel)
        return revision


class PanelCog:
    def __init__(self, cog, guild):
        self.cog, self.guild, self.bot = cog, guild, cog.bot
        self.store = PanelStore(cog, guild)

    def lock(self, ident):
        return self.cog.lock(self.guild)

    async def post(self, target, data, *, private=False):
        return await self.cog.post(target, data | {"_guild": self.guild}, private=private)

    async def publish(self, i, ident, revision):
        return await self.cog.publish(i, ident, revision)


class Form(discord.ui.Modal):
    def __init__(self, menu, *, colors=False):
        super().__init__(title="Title & line colors" if colors else "Edit removal button")
        self.menu, self.revision, self.colors = menu, menu.revision, colors
        self.fields = {}
        specs = (
            (
                ("text_color", "KY BOT headings hex (blank = preset)", 7),
                ("line_color", "Title line hex (blank follows title)", 7),
                ("header_text_color", "Header image title hex (blank = preset)", 7),
                ("footer_text_color", "Footer image text hex (blank = preset)", 7),
            )
            if colors
            else (
                ("button_heading", "KY BOT button heading (blank hides it)", 80),
                ("button_text", "Description beside the button", 1000),
                ("button_label", "Button label", 80),
            )
        )
        values = menu.config["panel"] if colors else menu.config
        for key, label, limit in specs:
            field = discord.ui.TextInput(
                label=label,
                default=values[key] or "",
                max_length=limit,
                required=not colors and key != "button_heading",
                style=discord.TextStyle.paragraph if limit > 80 else discord.TextStyle.short,
            )
            self.add_item(field)
            self.fields[key] = field

    async def on_submit(self, i):
        if not await self.menu.interaction_check(i):
            return
        await i.response.defer()
        try:
            values = {key: str(field) for key, field in self.fields.items()}
            if self.colors:
                from ky_bot.design.text_colour import text_colour

                values = {
                    "panel": self.menu.config["panel"]
                    | {key: text_colour(value) for key, value in values.items()}
                }
            await self.menu.cog.store.configure(self.menu.guild, self.revision, **values)
            await self.menu.show(i, "Saved.")
        except ValueError as error:
            await send_private(i, str(error))

    async def on_error(self, i, error):
        await report_error(i, error)


class Admin(discord.ui.LayoutView):
    def __init__(self, cog, owner, guild, *, parent=None):
        super().__init__(timeout=600)
        self.cog, self.owner, self.guild, self.parent = cog, owner, guild, parent
        self.revision, self.config = None, None

    async def interaction_check(self, i):
        try:
            if i.user.id != self.owner or i.guild_id != self.guild:
                raise ValueError("Open your own media removal settings.")
            await administrator(i)
            self.parent = menu_parent(self.parent, i)
            if self.parent and not await self.parent.interaction_check(i):
                return False
            return True
        except ValueError as error:
            await send_private(i, str(error))
            return False

    async def on_error(self, i, error, item):
        if isinstance(error, ValueError):
            await send_private(i, str(error))
        else:
            await report_error(i, error)

    async def show(self, i, notice=None):
        self.revision, self.config = await self.cog.store.config(self.guild)
        self.clear_items()
        panel = discord.ui.Container(accent_colour=0xD6E2F3)
        panel.add_item(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://media_settings_header.png"))
        )
        if notice:
            panel.add_item(discord.ui.TextDisplay(notice))

        def button(label, action, *, modal=False, disabled=False):
            item = discord.ui.Button(label=label, disabled=disabled)

            async def click(event):
                if not await self.interaction_check(event):
                    return
                if not modal:
                    await event.response.defer()
                await action(event)

            item.callback = click
            return item

        def row(title, text, control):
            panel.add_item(discord.ui.Section(f"**{title}**\n{text}", accessory=control))

        panel.add_item(
            discord.ui.TextDisplay(
                "**PANEL CHANNEL**\nChoose where members open their private "
                "cleanup options. Clear it to disable the feature."
            )
        )
        channel = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.text],
            min_values=0,
            placeholder="Choose media removal panel channel",
            default_values=[discord.Object(id=self.config["channel"])]
            if self.config["channel"]
            else [],
        )

        async def choose(event):
            if not await self.interaction_check(event):
                return
            await event.response.defer()
            value = channel.values[0].id if channel.values else 0
            if value:
                from ky_bot.confessions.command import destination

                destination(event.guild, value)
            await self.cog.store.configure(
                self.guild, self.revision, channel=value, enabled=bool(value)
            )
            await self.show(event, "Saved. Preview and publish when you are ready.")

        channel.callback = choose
        panel.add_item(discord.ui.ActionRow(channel))

        async def toggle(event):
            await self.cog.store.configure(
                self.guild, self.revision, enabled=not self.config["enabled"]
            )
            await self.show(event, "Saved. Disabling also pauses running cleanups.")

        row(
            "MEDIA REMOVAL STATUS",
            "Enabled" if self.config["enabled"] else "Disabled until a channel is selected.",
            button(
                "Disable" if self.config["enabled"] else "Enable",
                toggle,
                disabled=not self.config["channel"],
            ),
        )

        async def appearance(event):
            async def return_to(click):
                if not click.response.is_done():
                    await click.response.defer()
                await self.show(click)

            session = Session(
                PanelCog(self.cog, self.guild),
                self.owner,
                self.guild,
                str(self.guild),
                parent=self.parent,
                return_to=return_to,
            )
            await session.show(event)

        row(
            "PANEL TEXT & ARTWORK",
            "Edit every heading and paragraph, the KY BOT title, header/footer images and colors.",
            button("Edit panel", appearance),
        )
        row(
            "TITLE & LINE COLORS",
            "KY BOT headings, their lines, and header/footer image text.",
            button(
                "Edit title colors",
                lambda event: event.response.send_modal(Form(self, colors=True)),
                modal=True,
            ),
        )
        row(
            "BUTTON HEADING & TEXT",
            "Edit the READY TO CLEAR YOUR MEDIA? heading, description and button label.",
            button("Edit text", lambda event: event.response.send_modal(Form(self)), modal=True),
        )

        async def position(event):
            await self.cog.store.configure(
                self.guild, self.revision, inline=not self.config["inline"]
            )
            await self.show(event, "Saved.")

        row(
            "BUTTON POSITION",
            "Beside the description" if self.config["inline"] else "Below the description",
            button("Change", position),
        )

        async def preview(event):
            await self.cog.post(
                event.followup, self.config["panel"] | {"_guild": self.guild}, private=True
            )

        row(
            "PREVIEW PANEL",
            "Private sample; its removal button is disabled.",
            button("Preview", preview),
        )

        async def publish(event):
            notice = await self.cog.publish(event, str(self.guild), self.revision)
            await self.show(event, notice)

        row(
            "PUBLISH PANEL",
            "Post or update the member panel in the selected channel.",
            button(
                "Publish",
                publish,
                disabled=not self.config["channel"] or not self.config["enabled"],
            ),
        )
        panel.add_item(
            discord.ui.TextDisplay(
                "**CLEANUP SCOPE**\nEach member can remove only their "
                "own uploaded images/video posts across this server. "
                "Captions on those posts are removed too. Text-only "
                "messages, GIF-only posts, audio, stickers and links "
                "stay. Missing access is reported as a partial result; "
                "no media files or message text are stored."
            )
        )

        async def back(event):
            if self.parent:
                self.parent.section = "community"
                await self.parent.refresh(event)
            else:
                await event.edit_original_response(
                    view=discord.ui.LayoutView().add_item(
                        discord.ui.TextDisplay("Media removal settings closed.")
                    )
                )

        panel.add_item(
            discord.ui.ActionRow(
                button("Back" if self.parent else "Close", back),
                button("Refresh", lambda event: self.show(event)),
            )
        )
        panel.add_item(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://media_settings_footer.png"))
        )
        group_sections(panel, "media_removal")
        section_dividers(panel, navigation_on_right=True)
        self.add_item(panel)
        profile = default_profile("settings:media_removal")
        header, footer = await asyncio.to_thread(render_menu, profile, "MEDIA REMOVAL")
        files = [
            discord.File(io.BytesIO(header), filename="media_settings_header.png"),
            discord.File(io.BytesIO(footer), filename="media_settings_footer.png"),
        ] + strip_files("media_removal")
        try:
            await display_menu(i, self, files, self.parent)
        finally:
            for file in files:
                file.close()
