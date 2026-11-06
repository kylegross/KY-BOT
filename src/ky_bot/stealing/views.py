"""Private, paginated multi-select picker with per-asset naming."""

import asyncio
import io
import time
from importlib.resources import files

import aiohttp
import discord

from ky_bot.errors import report_error, send_private
from ky_bot.views.media import NoAltMediaGalleryItem
from ky_bot.views.menu_strips import add_strip, strip_files
from ky_bot.views.sections import section_dividers

from .assets import name_for
from .command import DestinationError


def safe(value):
    return discord.utils.escape_mentions(discord.utils.escape_markdown(str(value)))


def _base_artwork():
    return [
        discord.File(
            io.BytesIO(files("ky_bot.design").joinpath("assets", folder, name).read_bytes()),
            filename=name,
        )
        for folder, name in (("header", "ky_header_steal.png"), ("footer", "ky_footer_steal.png"))
    ]


async def show(interaction, view, *, attachments=False):
    uploads = artwork() if attachments else []
    try:
        kwargs = dict(
            content=None, embeds=[], view=view, allowed_mentions=discord.AllowedMentions.none()
        )
        if attachments:
            kwargs["attachments"] = uploads
        return await interaction.edit_original_response(**kwargs)
    finally:
        for file in uploads:
            file.close()


def button(label, callback, *, disabled=False):
    item = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary, disabled=disabled)
    item.callback = callback
    return item


class Rename(discord.ui.Modal):
    def __init__(self, batch, asset):
        super().__init__(title=f"Rename {asset.kind}")
        self.batch, self.asset = batch, asset
        self.name = discord.ui.TextInput(
            label="Custom name · blank keeps the original",
            required=False,
            default=batch.names.get(asset.key, asset.name or ""),
            max_length=32 if asset.kind == "emoji" else 30,
        )
        self.add_item(self.name)

    async def on_submit(self, i):
        batch = self.batch
        if not await batch.interaction_check(i):
            return
        value = self.name.value.strip()
        if value:
            try:
                batch.names[self.asset.key] = name_for(self.asset, value)
            except ValueError as error:
                return await send_private(i, str(error))
        else:
            batch.names.pop(self.asset.key, None)
        await i.response.defer()
        batch.mode = "pick"
        batch.build()
        await show(i, batch)

    async def on_error(self, i, error):
        await report_error(i, error)


class Batch(discord.ui.LayoutView):
    def __init__(self, cog, owner, origin_guild, origin_channel, destination, guild_name, assets):
        super().__init__(timeout=900)
        self.cog, self.owner = cog, owner
        self.origin_guild, self.origin_channel = origin_guild, origin_channel
        self.destination, self.guild_name = destination, guild_name
        self.assets = {a.key: a for a in assets}
        self.selected = set(self.assets)
        self.names, self.results, self.completed, self.uncertain = {}, {}, set(), set()
        self.page, self.mode = 0, "pick"
        self.working = False
        self.lock = asyncio.Lock()
        self.notice = None
        self.build()

    async def interaction_check(self, i):
        if (
            i.user.id != self.owner
            or i.guild_id != self.origin_guild
            or i.channel_id != self.origin_channel
        ):
            await send_private(i, "Run your own /steal command to use a private picker.")
            return False
        if self.working:
            await send_private(
                i, "Your selected assets are being copied. Please wait for the results."
            )
            return False
        return True

    def available(self):
        return [a for key, a in self.assets.items() if key not in self.completed | self.uncertain]

    def build(self):
        self.clear_items()
        panel = discord.ui.Container(accent_colour=0xD6E2F3)
        panel.add_item(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://ky_header_steal.png"))
        )
        add_strip(panel, "DESTINATION")
        panel.add_item(
            discord.ui.TextDisplay(
                f"{safe(self.guild_name)} · Server ID: `{self.destination}`\n"
                "Choose the items to copy. The checkmarks in the dropdown control your selection. "
                "All items start selected; uncheck anything you do not want."
            )
        )
        if self.notice:
            panel.add_item(discord.ui.TextDisplay(self.notice))
        available = self.available()
        pages = max(1, (len(available) + 24) // 25)
        self.page = min(self.page, pages - 1)
        items = available[self.page * 25 : (self.page + 1) * 25]
        if items:
            add_strip(panel, "SELECT ASSETS")
            select = discord.ui.Select(
                placeholder="Check emojis / stickers to copy"
                if self.mode == "pick"
                else "Choose one asset to rename",
                min_values=0 if self.mode == "pick" else 1,
                max_values=len(items) if self.mode == "pick" else 1,
                options=[
                    discord.SelectOption(
                        label=f"{a.kind.title()} · {self.names.get(a.key, a.name or 'Unnamed')}"[
                            :100
                        ],
                        value=a.key,
                        description=("Animated" if a.animated else "Static") + f" · ID {a.ident}",
                        default=self.mode == "pick" and a.key in self.selected,
                        emoji=discord.PartialEmoji(
                            name=a.name or "emoji", id=a.ident, animated=a.animated
                        )
                        if a.kind == "emoji"
                        else "🖼️",
                    )
                    for a in items
                ],
            )

            async def choose(i):
                if self.mode == "rename":
                    key = select.values[0]
                    if key not in {a.key for a in items}:
                        return await send_private(i, "Choose an asset from this page.")
                    return await i.response.send_modal(Rename(self, self.assets[key]))
                page_keys = {a.key for a in items}
                self.selected = (self.selected - page_keys) | (set(select.values) & page_keys)
                self.build()
                await i.response.edit_message(view=self)

            select.callback = choose
            panel.add_item(discord.ui.ActionRow(select))
        else:
            panel.add_item(discord.ui.TextDisplay("No uncopied assets remain in this picker."))
        if pages > 1:

            async def previous(i):
                self.page -= 1
                self.build()
                await i.response.edit_message(view=self)

            async def following(i):
                self.page += 1
                self.build()
                await i.response.edit_message(view=self)

            panel.add_item(
                discord.ui.ActionRow(
                    button("Previous", previous, disabled=self.page == 0),
                    button("Next", following, disabled=self.page == pages - 1),
                )
            )
        if self.mode == "pick":
            selected = self.selected - self.completed - self.uncertain
            panel.add_item(
                discord.ui.Section(
                    f"**COPY SELECTED · {len(selected)}**\nOnly checked items will be uploaded.",
                    accessory=button("Copy selected", self.copy_selected, disabled=not selected),
                )
            )

            async def rename(i):
                self.mode = "rename"
                self.build()
                await i.response.edit_message(view=self)

            add_strip(panel, "CUSTOM NAMES")
            panel.add_item(
                discord.ui.Section(
                    "Leave an item unchanged to keep its original name.",
                    accessory=button("Rename one", rename, disabled=not available),
                )
            )

            async def all_items(i):
                self.selected = {a.key for a in self.available()}
                self.build()
                await i.response.edit_message(view=self)

            async def clear(i):
                self.selected.clear()
                self.build()
                await i.response.edit_message(view=self)

            panel.add_item(
                discord.ui.ActionRow(button("Select all", all_items), button("Clear checks", clear))
            )
        else:

            async def back(i):
                self.mode = "pick"
                self.build()
                await i.response.edit_message(view=self)

            panel.add_item(
                discord.ui.Section(
                    "**ASSET PICKER**\nReturn to the checkboxes.", accessory=button("Back", back)
                )
            )
        if self.results:
            add_strip(panel, "RESULTS")
            lines = [f"**{len(self.completed)} copied** · {len(self.uncertain)} unconfirmed"]
            for key, result in list(self.results.items())[-8:]:
                label = self.names.get(key, self.assets[key].name or self.assets[key].ident)
                lines.append(f"{safe(label)} — {safe(result)[:180]}")
            panel.add_item(discord.ui.TextDisplay("\n".join(lines)))

        async def close(i):
            view = discord.ui.LayoutView()
            view.add_item(
                discord.ui.TextDisplay("Picker closed. Completed copies stay in your destination.")
            )
            await i.response.edit_message(view=view, attachments=[])
            view.stop()
            self.stop()

        panel.add_item(
            discord.ui.Section(
                "**PRIVATE CONTROLS**\nUnchecked items are never copied.",
                accessory=button("Close", close),
            )
        )
        section_dividers(panel)
        panel.add_item(
            discord.ui.MediaGallery(NoAltMediaGalleryItem("attachment://ky_footer_steal.png"))
        )
        self.add_item(panel)

    async def copy_selected(self, i):
        if not await self.interaction_check(i):
            return
        await i.response.defer()
        async with self.lock:
            selected = [
                self.assets[key]
                for key in self.assets
                if key in self.selected and key not in self.completed | self.uncertain
            ]
            if not selected:
                return await send_private(i, "Check at least one item to copy.")
            self.working = True
            deadline = time.monotonic() + 600
            try:
                for asset in selected:
                    if time.monotonic() >= deadline:
                        self.notice = (
                            "Time limit reached. Choose Copy selected again "
                            "for the remaining items."
                        )
                        break
                    try:
                        _, name = await self.cog.copy(
                            self.owner, self.destination, asset, self.names.get(asset.key)
                        )
                        self.completed.add(asset.key)
                        self.selected.discard(asset.key)
                        self.results[asset.key] = f"Copied as {name}"
                    except DestinationError as error:
                        self.notice = safe(str(error))
                        break
                    except (TimeoutError, aiohttp.ClientError):
                        self.uncertain.add(asset.key)
                        self.selected.discard(asset.key)
                        self.results[asset.key] = (
                            "Not confirmed. Check the destination before retrying."
                        )
                        break
                    except ValueError as error:
                        self.results[asset.key] = str(error)
                    except discord.HTTPException as error:
                        if error.status >= 500:
                            self.uncertain.add(asset.key)
                            self.selected.discard(asset.key)
                            self.results[asset.key] = (
                                "Not confirmed. Check the destination before retrying."
                            )
                            break
                        errors = {
                            30008: "The server has no free emoji slots.",
                            30018: "The server has no free animated emoji slots.",
                            30039: "The server has no free sticker slots.",
                            50013: "Discord denied expression creation. Recheck permissions.",
                        }
                        self.results[asset.key] = errors.get(
                            error.code,
                            "Discord rejected this asset or name. Try a valid name "
                            "or check its upload limits.",
                        )
                    self.build()
                    await show(i, self)
            finally:
                self.working = False
            self.build()
            await show(i, self)

    async def on_error(self, i, error, item):
        await report_error(i, error)


def artwork():
    return _base_artwork() + strip_files("steal", "silver_neon")
