import asyncio
import base64
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from PIL import Image

from ky_bot.checklists.artwork import title_image
from ky_bot.custom_embeds.model import defaults, image_upload, render, validate
from ky_bot.custom_embeds.store import Store
from ky_bot.custom_embeds.views import EditModal, Editor, Session
from ky_bot.design.editor import AppearanceModal, DesignSession
from ky_bot.design.editor import Editor as DesignEditor
from ky_bot.design.footers import FooterError, render_footer
from ky_bot.design.headers import render_header
from ky_bot.design.menu_design import default_profile, render_menu
from ky_bot.design.text_colour import text_colour


class DecorativeTextTests(unittest.IsolatedAsyncioTestCase):
    def png(self):
        out = io.BytesIO()
        Image.new("RGBA", (50, 50), "#101010").save(out, format="PNG")
        return out.getvalue()

    def interaction(self):
        return SimpleNamespace(
            user=SimpleNamespace(id=50),
            guild_id=1,
            guild=SimpleNamespace(),
            client=SimpleNamespace(),
            permissions=SimpleNamespace(administrator=True),
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_color_validation_and_legacy_defaults(self):
        self.assertEqual(text_colour("#39dccb"), "#39DCCB")
        self.assertEqual(text_colour("000000"), "#000000")
        self.assertIsNone(text_colour(""))
        for bad in ("red", "#FFF", "#GGGGGG"):
            with self.assertRaises(ValueError):
                validate(dict(text_color=bad))
        old = defaults()
        for key in ("text_color", "text_finish", "sections"):
            del old[key]
        data = validate(old)
        self.assertIsNone(data["text_color"])
        self.assertEqual(data["sections"], [])

    async def test_section_validation_and_total_message_limit(self):
        validate(
            dict(sections=[dict(heading="Rules", body="Read these")], channel=10), publish=True
        )
        for sections in (
            [{}],
            [dict(heading="One\nTwo")],
            [dict(heading="x" * 81)],
            [dict(heading="A", body="x" * 1501)],
            [dict(heading="A")] * 6,
            "invalid",
        ):
            with self.assertRaises(ValueError):
                validate(dict(sections=sections))
        with self.assertRaises(ValueError):
            validate(dict(body="x" * 3000, sections=[dict(heading="A", body="x" * 1100)]))
        with self.assertRaises(ValueError):
            validate(dict(title="x" * 81, title_style="decorative"))

    async def test_heading_pixels_use_requested_color(self):
        raw = title_image("Welcome", "gold", "#39DCCB")
        image = Image.open(io.BytesIO(raw)).convert("RGBA")
        visible = [p for p in image.get_flattened_data() if p[3] > 0]
        self.assertTrue(visible)
        self.assertTrue(all(p[:3] == (57, 220, 203) for p in visible))

    async def test_render_maximum_sections_and_decorative_fields(self):
        encoded = image_upload(self.png())
        data = dict(
            title="Welcome",
            title_style="decorative",
            body="Intro",
            body_style="decorative",
            footer_text="Thanks",
            footer_style="decorative",
            header=encoded,
            footer=encoded,
            text_color="#39DCCB",
            sections=[dict(heading=f"Section {n}", body=f"Message {n}") for n in range(5)],
        )
        view, attachments = render(data)
        try:
            self.assertLessEqual(view.total_children_count, 40)
            self.assertEqual(len(attachments), 10)
            names = [f.filename for f in attachments]
            self.assertEqual(names[0], "custom_header.png")
            self.assertEqual(names[-1], "custom_footer.png")
            self.assertEqual(len(names), len(set(names)))
            payload = str(view.to_components())
            self.assertLess(payload.index("Message 0"), payload.index("Message 4"))
            self.assertIn("description", payload)
        finally:
            view.stop()
            for file in attachments:
                file.close()

    async def test_artwork_color_is_independent_of_custom_background(self):
        profile = default_profile("checklist") | dict(
            header_background=base64.b64encode(self.png()).decode(),
            footer_background=base64.b64encode(self.png()).decode(),
            footer_text="Thanks",
            text_color="#39DCCB",
        )
        header, footer = render_menu(profile, "Welcome")
        default_header, default_footer = render_menu(profile | dict(text_color=None), "Welcome")
        self.assertNotEqual(header, default_header)
        self.assertNotEqual(footer, default_footer)
        self.assertNotEqual(
            render_footer(self.png(), "gold", text_color="#39DCCB"),
            render_footer(self.png(), "gold"),
        )
        with self.assertRaises(FooterError):
            render_header(self.png(), text_color="red")
        with self.assertRaises(FooterError):
            render_footer(self.png(), "gold", centered=True, text_color="red")

    async def test_sections_save_edit_remove_and_reject_stale_modal(self):
        store = await Store.open(":memory:")
        try:
            ident = await store.create(1)
            cog = SimpleNamespace(store=store, lock=lambda _: asyncio.Lock())
            session = Session(cog, 50, 1, ident)
            session.row = await store.get(1, ident)
            session.show = AsyncMock()
            add = EditModal(session, "section_add")
            add.fields["heading"]._value = "Rules"
            add.fields["body"]._value = "Read carefully"
            await add.on_submit(self.interaction())
            session.row = await store.get(1, ident)
            self.assertEqual(session.row["draft"]["sections"][0]["heading"], "Rules")
            self.assertIsNone(session.row["published"])
            edit = EditModal(session, "section_edit")
            stale = EditModal(session, "section_edit")
            edit.fields["heading"]._value = "Updated"
            await edit.on_submit(self.interaction())
            session.row = await store.get(1, ident)
            stale.fields["heading"]._value = "Stale"
            await stale.on_submit(self.interaction())
            session.row = await store.get(1, ident)
            self.assertEqual(session.row["draft"]["sections"][0]["heading"], "Updated")
            session.page = "sections"
            view = Editor(session)
            remove = next(
                x
                for x in view.walk_children()
                if isinstance(x, discord.ui.Button) and x.label == "Remove section"
            )
            await remove.callback(self.interaction())
            self.assertEqual((await store.get(1, ident))["draft"]["sections"], [])
            view.stop()
            for modal in (add, edit, stale):
                modal.stop()
        finally:
            await store.close()

    async def test_color_modal_reset_and_published_snapshot_keep_sections(self):
        store = await Store.open(":memory:")
        try:
            ident = await store.create(1, 10)
            cog = SimpleNamespace(store=store, lock=lambda _: asyncio.Lock())
            session = Session(cog, 50, 1, ident)
            session.row = await store.get(1, ident)
            session.show = AsyncMock()
            modal = EditModal(session, "text_color")
            modal.fields["text_color"]._value = "#000000"
            await modal.on_submit(self.interaction())
            session.row = await store.get(1, ident)
            self.assertEqual(session.row["draft"]["text_color"], "#000000")
            await store.save(
                1,
                ident,
                session.row["revision"],
                sticky_every=1,
                sections=[dict(heading="Rules", body="Read these")],
            )
            row = await store.get(1, ident)
            await store.published(1, ident, 0, row["draft"], 10, 100)
            await store.save(1, ident, row["revision"], sections=[])
            row = await store.get(1, ident)
            self.assertEqual(row["published"]["sections"][0]["heading"], "Rules")
            self.assertEqual(row["published"]["text_color"], "#000000")
            self.assertEqual(row["draft"]["sections"], [])
            session.row = row
            reset = EditModal(session, "text_color")
            reset.fields["text_color"]._value = ""
            await reset.on_submit(self.interaction())
            self.assertIsNone((await store.get(1, ident))["draft"]["text_color"])
            modal.stop()
            reset.stop()
        finally:
            await store.close()

    async def test_new_editor_pages_and_modals_fit_and_maximum_disables_add(self):
        session = Session(None, 50, 1, "draft")
        session.row = dict(
            revision="rev",
            deleted=0,
            message=0,
            draft=defaults()
            | dict(sections=[dict(heading=f"Section {n}", body="Text") for n in range(5)]),
        )
        for page in ("main", "formatting", "sections", "content"):
            session.page = page
            view = Editor(session, "Draft saved.")
            self.assertLessEqual(view.total_children_count, 40)
            if page == "sections":
                add = next(
                    x
                    for x in view.walk_children()
                    if isinstance(x, discord.ui.Button) and x.label == "+ Add section"
                )
                self.assertTrue(add.disabled)
            view.stop()
        for mode in ("section_add", "section_edit", "text_color"):
            modal = EditModal(session, mode)
            self.assertLessEqual(len(modal.children), 5)
            modal.stop()
        design = DesignSession(self.interaction(), None, 0, {})
        await design.select(["checklist"])
        view = DesignEditor(design, notice="Draft saved.")
        self.assertLessEqual(view.total_children_count, 40)
        view.stop()
        modal = AppearanceModal(design, formatting=True)
        self.assertEqual(set(modal.fields), {"text_color"})
        modal.stop()


if __name__ == "__main__":
    unittest.main()
