import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from PIL import Image, ImageDraw

from ky_bot.tickets.artwork import normalize, public_files
from ky_bot.tickets.command import Tickets
from ky_bot.tickets.presentation import heading_file, published_layout, visible_types
from ky_bot.tickets.store import Store, defaults, new_panel, new_type, validate
from ky_bot.tickets.views import Admin, PanelPage, PublicPanel, TicketButton

PANEL = "a" * 32
KIND = "b" * 32


def config():
    value = defaults()
    value["panels"][PANEL] = new_panel("Support") | dict(
        types=[KIND], message=100, published_channel=200
    )
    value["types"][KIND] = new_type("Applications")
    return value


def rendered(panel):
    files = public_files(panel)
    try:
        return {file.filename: file.fp.read() for file in files}
    finally:
        for file in files:
            file.close()


class PanelEditingTests(unittest.IsolatedAsyncioTestCase):
    async def test_header_text_changes_the_artwork_independently(self):
        panel = new_panel("Support")
        before = rendered(panel)
        panel["header_title"] = "HELP DESK"
        after = rendered(panel)
        self.assertNotEqual(before["ticket_panel_header.png"], after["ticket_panel_header.png"])
        self.assertEqual(before["ticket_panel_footer.png"], after["ticket_panel_footer.png"])
        self.assertEqual(panel["title"], "Contact Staff")
        panel["header_title"] = ""
        blank = rendered(panel)
        self.assertNotEqual(blank["ticket_panel_header.png"], after["ticket_panel_header.png"])

    async def test_footer_supports_text_and_replacement_icon(self):
        panel = new_panel("Support")
        original = rendered(panel)["ticket_panel_footer.png"]
        panel.update(footer_content="text", footer_banner_text="HERE TO HELP")
        text = rendered(panel)["ticket_panel_footer.png"]
        self.assertNotEqual(original, text)
        image = Image.new("RGBA", (100, 100))
        ImageDraw.Draw(image).ellipse((20, 20, 80, 80), fill=(255, 0, 0, 255))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        panel.update(footer_content="icon", footer_icon=normalize(buffer.getvalue(), "footer_icon"))
        icon = rendered(panel)["ticket_panel_footer.png"]
        self.assertNotEqual(icon, text)
        self.assertNotEqual(icon, original)
        with self.assertRaises(ValueError):
            normalize(b"invalid icon", "footer_icon")

    async def test_uploaded_images_can_receive_header_or_footer_text(self):
        image = Image.new("RGBA", (600, 300), (30, 40, 50, 255))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        panel = new_panel("Support") | dict(
            header_mode="custom",
            header_custom=normalize(buffer.getvalue(), "header"),
            footer_mode="custom",
            footer_custom=normalize(buffer.getvalue(), "footer"),
        )
        before = rendered(panel)
        panel.update(
            header_title="CUSTOM HELP", footer_content="text", footer_banner_text="THANK YOU"
        )
        after = rendered(panel)
        self.assertNotEqual(before["ticket_panel_header.png"], after["ticket_panel_header.png"])
        self.assertNotEqual(before["ticket_panel_footer.png"], after["ticket_panel_footer.png"])

    async def test_section_and_button_text_and_icon_are_independent(self):
        value = config()
        value["types"][KIND].update(
            section_title="JOIN THE TEAM",
            description="Send your application here.",
            button_label="Apply now",
            emoji="📝",
            description_style="plain",
        )
        view = PublicPanel(value, PANEL)
        control = next(item for item in view.walk_children() if isinstance(item, TicketButton))
        self.assertEqual(control.item.label, "Apply now")
        self.assertEqual(str(control.item.emoji), "📝")
        text = "\n".join(
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("JOIN THE TEAM", text)
        self.assertIn("Send your application here.", text)
        self.assertNotIn("-# Send", text)
        self.assertEqual(value["types"][KIND]["name"], "Applications")
        view.stop()

    async def test_decorative_heading_uses_shared_ky_bot_artwork(self):
        value = config()
        value["types"][KIND].update(
            section_title="APPEALS", heading_style="decorative", heading_finish="red_iridescent"
        )
        files = public_files(value["panels"][PANEL], value, PANEL)
        view = PublicPanel(value, PANEL)
        try:
            names = {file.filename for file in files}
            self.assertIn(heading_file(KIND), names)
            text = "\n".join(
                item.content
                for item in view.walk_children()
                if isinstance(item, discord.ui.TextDisplay)
            )
            self.assertNotIn("APPEALS", text)
            self.assertIn("attachment://" + heading_file(KIND), str(view.to_components()))
        finally:
            for file in files:
                file.close()
            view.stop()

    async def test_custom_discord_formatting_is_preserved_and_empty_caption_is_removed(self):
        value = config()
        value["panels"][PANEL]["footer"] = ""
        value["types"][KIND].update(
            section_title="__**Apply Here**__",
            heading_style="custom",
            description="**Important**\nRead before applying.",
            description_style="custom",
        )
        view = PublicPanel(value, PANEL)
        text = [
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        ]
        self.assertTrue(any("__**Apply Here**__" in item for item in text))
        self.assertFalse(any(item.strip() == "-#" for item in text))
        self.assertTrue(any("**Important**" in item for item in text))
        view.stop()

    async def test_largest_decorative_panels_page_without_losing_sections(self):
        value = config()
        value["types"] = {
            format(n, "032x"): new_type(f"Topic {n}") | dict(heading_style="decorative")
            for n in range(25)
        }
        value["panels"][PANEL].update(
            types=list(value["types"]), image="https://example.com/body.png"
        )
        seen = []
        for page in range(5):
            view = PublicPanel(value, PANEL, page=page, preview=True)
            files = public_files(value["panels"][PANEL], value, PANEL, page)
            try:
                self.assertLessEqual(view.total_children_count, 40)
                self.assertLessEqual(view.content_length(), 4000)
                self.assertLessEqual(len(files), 10)
                names = {file.filename for file in files}
                entries, _, pages = visible_types(value, PANEL, page)
                self.assertEqual(pages, 5)
                seen.extend(key for key, _ in entries)
                for key, _ in entries:
                    self.assertIn(heading_file(key), names)
                self.assertTrue(
                    all(
                        item.item.disabled
                        for item in view.walk_children()
                        if isinstance(item, (TicketButton, PanelPage))
                    )
                )
            finally:
                for file in files:
                    file.close()
                view.stop()
        self.assertEqual(seen, value["panels"][PANEL]["types"])

    async def test_editor_pages_fit_with_the_new_controls(self):
        value = config()
        value["types"][KIND]["heading_style"] = "decorative"
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        for page, key in (
            ("type", KIND),
            ("type_appearance", KIND),
            ("type_lettering", KIND),
            ("art_header", PANEL),
            ("art_footer", PANEL),
        ):
            for pick in (None, "source", "finish", "content", "heading_style", "description_style"):
                view = Admin(
                    SimpleNamespace(),
                    parent,
                    None,
                    value,
                    page=page,
                    selected=key,
                    pick=pick,
                    notice="Saved",
                )
                self.assertLessEqual(view.total_children_count, 40)
                self.assertLessEqual(view.content_length(), 4000)
                view.stop()

    async def test_paging_rejects_retired_panels(self):
        store = await Store.open(":memory:")
        try:
            await store.configure(1, None, config())
            cog = Tickets(SimpleNamespace())
            cog.store = store
            i = SimpleNamespace(
                guild_id=1,
                message=SimpleNamespace(id=999),
                response=SimpleNamespace(defer=AsyncMock()),
            )
            with self.assertRaisesRegex(ValueError, "current message"):
                await cog.page_panel(i, PANEL, 1)
        finally:
            await store.close()

    async def test_model_rejects_missing_icons_and_invalid_formats(self):
        value = config()
        value["panels"][PANEL]["footer_content"] = "icon"
        with self.assertRaisesRegex(ValueError, "Upload a footer icon"):
            validate(value)
        value["panels"][PANEL]["footer_content"] = "emblem"
        value["types"][KIND]["heading_style"] = "not-a-style"
        with self.assertRaisesRegex(ValueError, "text style"):
            validate(value)

    async def test_published_layout_is_a_small_independent_snapshot(self):
        value = config()
        value["types"][KIND]["overrides"] = {"support_roles": [123]}
        snapshot = published_layout(value, PANEL)
        value["panels"][PANEL]["published_layout"] = snapshot
        value["types"][KIND]["section_title"] = "Draft heading"
        self.assertNotEqual(snapshot["types"][KIND]["section_title"], "Draft heading")
        self.assertNotIn("overrides", snapshot["types"][KIND])
        second = published_layout(value, PANEL)
        self.assertNotIn("published_layout", second["panel"])

    async def test_public_paging_does_not_publish_draft_edits(self):
        value = config()
        value["types"] = {format(n, "032x"): new_type(f"Topic {n}") for n in range(6)}
        value["panels"][PANEL]["types"] = list(value["types"])
        value["panels"][PANEL]["published_layout"] = published_layout(value, PANEL)
        value["types"][format(5, "032x")]["section_title"] = "UNPUBLISHED DRAFT"
        store = await Store.open(":memory:")
        try:
            await store.configure(1, None, value)
            cog = Tickets(SimpleNamespace())
            cog.store = store
            i = SimpleNamespace(
                guild_id=1,
                message=SimpleNamespace(id=100),
                response=SimpleNamespace(defer=AsyncMock()),
                edit_original_response=AsyncMock(),
            )
            await cog.page_panel(i, PANEL, 1)
            view = i.edit_original_response.await_args.kwargs["view"]
            text = "\n".join(
                item.content
                for item in view.walk_children()
                if isinstance(item, discord.ui.TextDisplay)
            )
            self.assertIn("Topic 5", text)
            self.assertNotIn("UNPUBLISHED DRAFT", text)
            view.stop()
        finally:
            await store.close()

    async def test_private_preview_paging_does_not_enable_ticket_opening(self):
        value = config()
        value["types"] = {format(n, "032x"): new_type(f"Topic {n}") for n in range(6)}
        value["panels"][PANEL]["types"] = list(value["types"])
        cog = Tickets(SimpleNamespace())
        i = SimpleNamespace(user=SimpleNamespace(id=5), followup=SimpleNamespace(send=AsyncMock()))
        await cog.preview_panel(i, value, PANEL)
        first = i.followup.send.await_args.kwargs["view"]
        next_button = next(
            item
            for item in first.walk_children()
            if isinstance(item, discord.ui.Button) and item.label == "Next"
        )
        self.assertFalse(next_button.disabled)
        self.assertTrue(
            all(
                item.item.disabled
                for item in first.walk_children()
                if isinstance(item, TicketButton)
            )
        )
        event = SimpleNamespace(
            user=SimpleNamespace(id=5),
            response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        await next_button.callback(event)
        second = event.edit_original_response.await_args.kwargs["view"]
        text = "\n".join(
            item.content
            for item in second.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("Topic 5", text)
        self.assertTrue(
            all(
                item.item.disabled
                for item in second.walk_children()
                if isinstance(item, TicketButton)
            )
        )
        first.stop()
        second.stop()
