import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from ky_bot.tickets.artwork import public_files
from ky_bot.tickets.command import Tickets
from ky_bot.tickets.presentation import panel_text, published_layout, text_file, visible_types
from ky_bot.tickets.store import Store, defaults, new_panel, new_type, validate
from ky_bot.tickets.views import Admin, PublicPanel

PANEL = "a" * 32
KIND = "b" * 32


def config():
    value = defaults()
    value["panels"][PANEL] = new_panel("Support") | dict(types=[KIND], message=100)
    value["types"][KIND] = new_type("Support")
    return value


class PanelTextTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_is_independently_styled_and_placed_around_buttons(self):
        value = config()
        value["panels"][PANEL].update(
            before_text="READ THIS FIRST",
            before_style="bold",
            after_text="Thank you for contacting us",
            after_style="italic",
        )
        view = PublicPanel(value, PANEL)
        components = str(view.to_components())
        self.assertLess(components.index("**READ THIS FIRST**"), components.index("ky:ticket:"))
        self.assertGreater(
            components.index("*Thank you for contacting us*"), components.index("ky:ticket:")
        )
        view.stop()

    async def test_both_blocks_can_be_removed_and_legacy_intro_is_preserved(self):
        value = config()
        panel = value["panels"][PANEL]
        self.assertEqual(panel_text(panel, "before"), panel["description"])
        panel.update(before_text="", after_text="")
        view = PublicPanel(value, PANEL)
        text = [
            item.content
            for item in view.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        ]
        self.assertFalse(any(panel["description"] in item for item in text))
        self.assertFalse(any(item.strip() == "-#" for item in text))
        view.stop()

    async def test_special_text_images_and_topic_headings_fit_together(self):
        value = config()
        value["types"] = {
            format(n, "032x"): new_type(f"Topic {n}") | dict(heading_style="decorative")
            for n in range(25)
        }
        panel = value["panels"][PANEL]
        panel.update(
            types=list(value["types"]),
            before_text="READ FIRST",
            before_style="decorative",
            after_text="THANK YOU",
            after_style="decorative",
            image="https://example.com/body.png",
        )
        pages = visible_types(value, PANEL)[2]
        self.assertGreater(pages, 5)
        for page in range(pages):
            view = PublicPanel(value, PANEL, page=page)
            files = public_files(panel, value, PANEL, page)
            try:
                self.assertLessEqual(view.total_children_count, 40)
                self.assertLessEqual(len(files), 10)
                names = {file.filename for file in files}
                self.assertIn(text_file("before"), names)
                self.assertIn(text_file("after"), names)
                for item in view.walk_children():
                    if isinstance(item, discord.ui.MediaGallery):
                        for media in item.items:
                            if media.media.url.startswith("attachment://"):
                                self.assertIn(media.media.url.removeprefix("attachment://"), names)
            finally:
                for file in files:
                    file.close()
                view.stop()

    async def test_long_text_gets_smaller_topic_pages(self):
        value = config()
        value["types"] = {
            format(n, "032x"): new_type("H" * 80) | dict(description="D" * 300) for n in range(10)
        }
        value["panels"][PANEL].update(
            types=list(value["types"]), before_text="A" * 1500, after_text="Z" * 1000
        )
        _, _, pages = visible_types(value, PANEL)
        self.assertGreater(pages, 2)
        for page in range(pages):
            view = PublicPanel(value, PANEL, page=page)
            self.assertLessEqual(view.content_length(), 4000)
            view.stop()

    async def test_text_editor_pages_keep_the_inline_layout(self):
        value = config()
        value["panels"][PANEL].update(
            before_text="HELLO",
            before_style="decorative",
            after_text="THANKS",
            after_style="decorative",
        )
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        for page in ("panel", "panel_text", "panel_text_before", "panel_text_after"):
            for pick in (None, "before_style", "after_style"):
                view = Admin(
                    SimpleNamespace(),
                    parent,
                    None,
                    value,
                    page=page,
                    selected=PANEL,
                    pick=pick,
                    notice="Saved",
                )
                self.assertLessEqual(view.total_children_count, 40)
                self.assertLessEqual(view.content_length(), 4000)
                view.stop()

    async def test_invalid_decorative_text_is_rejected_before_saving(self):
        value = config()
        value["panels"][PANEL].update(before_text="A" * 81, before_style="decorative")
        with self.assertRaisesRegex(ValueError, "one line"):
            validate(value)

    async def test_older_public_snapshots_do_not_expose_new_draft_text(self):
        value = config()
        layout = published_layout(value, PANEL)
        for key in list(layout["panel"]):
            if key.startswith(("before_", "after_")):
                del layout["panel"][key]
        value["panels"][PANEL].update(
            published_layout=layout,
            before_text="UNPUBLISHED BEFORE",
            after_text="UNPUBLISHED AFTER",
        )
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
            await cog.page_panel(i, PANEL, 0)
            view = i.edit_original_response.await_args.kwargs["view"]
            text = "\n".join(
                item.content
                for item in view.walk_children()
                if isinstance(item, discord.ui.TextDisplay)
            )
            self.assertNotIn("UNPUBLISHED", text)
            self.assertIn(value["panels"][PANEL]["description"], text)
            view.stop()
        finally:
            await store.close()
