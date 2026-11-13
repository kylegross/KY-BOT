import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from ky_bot.tickets.artwork import public_files
from ky_bot.tickets.presentation import published_layout, visible_types
from ky_bot.tickets.sections import blocks, image_file, new_section
from ky_bot.tickets.store import Store, defaults, new_panel, new_type, validate
from ky_bot.tickets.views import Admin, PublicPanel

PANEL = "a" * 32
KIND = "b" * 32


def config():
    value = defaults()
    value["panels"][PANEL] = new_panel("Support") | dict(types=[KIND])
    value["types"][KIND] = new_type("Support")
    return value


def public_text(view):
    return "\n".join(
        item.content for item in view.walk_children() if isinstance(item, discord.ui.TextDisplay)
    )


class TicketSectionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_main_headers_are_optional_and_do_not_add_dividers(self):
        value = config()
        value["panels"][PANEL].update(
            before_heading="NSFW ACCESS",
            after_heading="WHAT HAPPENS NEXT",
            after_text="Staff will review your request.",
        )
        view = PublicPanel(value, PANEL)
        text = public_text(view)
        self.assertIn("**NSFW ACCESS**", text)
        self.assertIn("**WHAT HAPPENS NEXT**", text)
        self.assertFalse(
            any(isinstance(item, discord.ui.Separator) for item in view.walk_children())
        )
        view.stop()
        value["panels"][PANEL].update(before_heading="", after_heading="")
        view = PublicPanel(value, PANEL)
        self.assertNotIn("NSFW ACCESS", public_text(view))
        self.assertIn("Staff will review", public_text(view))
        view.stop()

    async def test_additional_sections_render_on_the_correct_side_with_optional_rules(self):
        value = config()
        before = new_section() | dict(heading="Eligibility", body="You must be 18 or older.")
        after = new_section() | dict(
            heading="Privacy", body="Your request stays in a private ticket."
        )
        value["panels"][PANEL].update(before_sections=[before], after_sections=[after])
        view = PublicPanel(value, PANEL)
        rendered = str(view.to_components())
        self.assertLess(rendered.index("Eligibility"), rendered.index("ky:ticket:"))
        self.assertGreater(rendered.index("Privacy"), rendered.index("ky:ticket:"))
        self.assertEqual(
            sum(isinstance(item, discord.ui.Separator) for item in view.walk_children()), 2
        )
        view.stop()
        before["divider"] = after["divider"] = False
        view = PublicPanel(value, PANEL)
        self.assertFalse(
            any(isinstance(item, discord.ui.Separator) for item in view.walk_children())
        )
        view.stop()

    async def test_empty_sections_do_not_leave_a_divider_or_empty_marker(self):
        value = config()
        value["panels"][PANEL]["before_sections"] = [new_section() | dict(heading="", body="")]
        view = PublicPanel(value, PANEL)
        self.assertFalse(
            any(isinstance(item, discord.ui.Separator) for item in view.walk_children())
        )
        self.assertFalse(any(line.strip() == "-#" for line in public_text(view).splitlines()))
        view.stop()

    async def test_heading_and_body_formats_are_independent(self):
        value = config()
        section = new_section() | dict(
            heading="Read first",
            heading_style="italic",
            body="**Keep this formatting**",
            body_style="custom",
            divider=False,
        )
        value["panels"][PANEL]["before_sections"] = [section]
        view = PublicPanel(value, PANEL)
        self.assertIn("*Read first*", public_text(view))
        self.assertIn("**Keep this formatting**", public_text(view))
        view.stop()

    async def test_decorative_headers_include_their_images_and_page_safely(self):
        value = config()
        value["types"] = {
            format(n, "032x"): new_type(f"Topic {n}") | dict(heading_style="decorative")
            for n in range(25)
        }
        panel = value["panels"][PANEL]
        panel.update(
            types=list(value["types"]),
            before_heading="READ FIRST",
            before_heading_style="decorative",
            after_heading="THANK YOU",
            after_heading_style="decorative",
            after_text="We will be in touch.",
        )
        panel["before_sections"] = [
            new_section()
            | dict(heading="ELIGIBILITY", heading_style="decorative", body="Adults only.")
        ]
        panel["after_sections"] = [
            new_section()
            | dict(heading="PRIVACY", heading_style="decorative", body="Private ticket.")
        ]
        validate(value)
        pages = visible_types(value, PANEL)[2]
        self.assertGreater(pages, 1)
        for page in range(pages):
            view = PublicPanel(value, PANEL, page=page)
            files = public_files(panel, value, PANEL, page)
            try:
                self.assertLessEqual(view.total_children_count, 40)
                self.assertLessEqual(len(files), 10)
                names = {file.filename for file in files}
                for position in ("before", "after"):
                    for block in blocks(panel, position):
                        if block["heading"]:
                            self.assertIn(image_file(position, block, "heading"), names)
            finally:
                for file in files:
                    file.close()
                view.stop()

    async def test_builder_can_add_toggle_and_remove_a_draft_section(self):
        store = await Store.open(":memory:")
        try:
            value = config()
            revision = await store.configure(1, None, value)
            cog = SimpleNamespace(store=store)
            parent = SimpleNamespace(panel_manager=None, guild_id=1)
            i = SimpleNamespace(
                response=SimpleNamespace(is_done=Mock(return_value=False), defer=AsyncMock())
            )
            view = Admin(cog, parent, revision, value, page="text_before", selected=PANEL)
            view.show = AsyncMock()
            add = next(
                item
                for item in view.walk_children()
                if isinstance(item, discord.ui.Button) and item.label == "Add section"
            )
            await add.callback(i)
            self.assertEqual(view.show.await_args.kwargs["offset"], 1)
            revision, value = await store.config(1)
            self.assertTrue(value["panels"][PANEL]["before_sections"][0]["divider"])
            view.stop()
            view = Admin(cog, parent, revision, value, page="text_before", selected=PANEL, offset=1)
            view.show = AsyncMock()
            toggle = next(
                item
                for item in view.walk_children()
                if isinstance(item, discord.ui.Button) and item.label == "Toggle"
            )
            await toggle.callback(i)
            revision, value = await store.config(1)
            self.assertFalse(value["panels"][PANEL]["before_sections"][0]["divider"])
            view.stop()
            view = Admin(cog, parent, revision, value, page="text_before", selected=PANEL, offset=1)
            view.show = AsyncMock()
            remove = next(
                item
                for item in view.walk_children()
                if isinstance(item, discord.ui.Button) and item.label == "Remove"
            )
            await remove.callback(i)
            self.assertEqual((await store.config(1))[1]["panels"][PANEL]["before_sections"], [])
            view.stop()
        finally:
            await store.close()

    async def test_section_editor_pages_fit_and_keep_selected_section(self):
        value = config()
        value["panels"][PANEL]["before_sections"] = [new_section()]
        value["panels"][PANEL]["after_sections"] = [new_section()]
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        for page in (
            "panel_text",
            "text_before",
            "text_after",
            "text_format_before",
            "text_format_after",
            "panel_text_before",
            "panel_text_after",
        ):
            for index in (0, 1):
                for pick in (None, "heading_style", "body_style"):
                    view = Admin(
                        SimpleNamespace(),
                        parent,
                        None,
                        value,
                        page=page,
                        selected=PANEL,
                        offset=index,
                        pick=pick,
                        notice="Saved",
                    )
                    self.assertLessEqual(view.total_children_count, 40)
                    self.assertLessEqual(view.content_length(), 4000)
                    view.stop()

    async def test_sections_are_in_published_snapshots_and_limits_are_validated(self):
        value = config()
        value["panels"][PANEL]["before_sections"] = [new_section()]
        snapshot = published_layout(value, PANEL)
        self.assertEqual(len(snapshot["panel"]["before_sections"]), 1)
        value["panels"][PANEL]["before_sections"].append(
            value["panels"][PANEL]["before_sections"][0].copy()
        )
        with self.assertRaises(ValueError):
            validate(value)
        value["panels"][PANEL]["before_sections"] = [new_section() for _ in range(4)]
        with self.assertRaisesRegex(ValueError, "three"):
            validate(value)
