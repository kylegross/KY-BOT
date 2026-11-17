import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from navigation_audit import assert_footer_dividers

from ky_bot.advice.menus import Admin as AdviceAdmin
from ky_bot.advice.store import Store as AdviceStore
from ky_bot.confessions.menus import Admin as ConfessionAdmin
from ky_bot.confessions.store import Store as ConfessionStore
from ky_bot.custom_embeds.model import defaults as embed_defaults
from ky_bot.custom_embeds.views import EditModal, Session
from ky_bot.custom_embeds.views import Editor as EmbedEditor
from ky_bot.design.editor import AppearanceModal, DesignSession
from ky_bot.design.editor import Editor as DesignEditor
from ky_bot.design.menu_design import default_profile, render_menu
from ky_bot.tickets.store import defaults, new_panel, new_type
from ky_bot.tickets.views import Admin as TicketAdmin
from ky_bot.tickets.views import PublicPanel


def event():
    return SimpleNamespace(
        user=SimpleNamespace(id=7),
        guild_id=1,
        permissions=discord.Permissions(administrator=True),
        client=SimpleNamespace(),
        guild=SimpleNamespace(
            fetch_member=AsyncMock(
                return_value=SimpleNamespace(
                    guild_permissions=discord.Permissions(administrator=True)
                )
            )
        ),
        response=SimpleNamespace(defer=AsyncMock(), send_modal=AsyncMock(), is_done=lambda: False),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


def buttons(view):
    return [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]


class FormattingConsistencyTests(unittest.IsolatedAsyncioTestCase):
    def assert_editor(self, view, *, image=False):
        view.to_components()
        self.assertLessEqual(view.total_children_count, 40)
        assert_footer_dividers(self, view)
        labels = [item.label for item in buttons(view)]
        self.assertIn("Preview", labels)
        if image:
            self.assertIn("Saved colors", labels)
            self.assertIn("Custom colors", labels)
            self.assertTrue(any("text & color" in label.lower() for label in labels))

    async def test_confessions_and_advice_each_image_and_formatting_page(self):
        for store_type, menu_type, module, root in (
            (ConfessionStore, ConfessionAdmin, "confessions", "appearance"),
            (AdviceStore, AdviceAdmin, "advice", "style"),
        ):
            store = await store_type.open(":memory:")
            try:
                await store.configure(
                    1,
                    10,
                    None,
                    header_text_color="#18AA77",
                    footer_text_color="#EE7799",
                    footer_image_text="Community",
                )
                menu = menu_type(SimpleNamespace(store=store), 7, 1)
                menu.channel = 10
                i = event()
                with patch("ky_bot." + module + ".menus.display_menu", new=AsyncMock()):
                    for page in (root, "header_artwork", "footer_artwork"):
                        with self.subTest(feature=module, page=page):
                            await menu.show(i, page, "Saved.")
                            # Advice exposes custom colors in its image-color selector.
                            self.assert_editor(menu, image=module == "confessions" and page != root)
                            if page == root:
                                text = str(menu.to_components())
                                self.assertLess(
                                    text.index("HEADER IMAGE"), text.index("FOOTER IMAGE")
                                )
                            if module == "advice" and page != root:
                                self.assertIn("Saved colors", [b.label for b in buttons(menu)])
                                choices = [
                                    option.label
                                    for child in menu.walk_children()
                                    if isinstance(child, discord.ui.Select)
                                    for option in child.options
                                ]
                                self.assertIn("Custom colors…", choices)
                    if module == "confessions":
                        for page in ("artwork", "lettering", "image_text"):
                            await menu.show(i, page)
                            self.assert_editor(menu)
                    else:
                        for page in ("sections", "section", "row", "text", "lettering"):
                            await menu.show(i, page)
                            self.assert_editor(menu)
                menu.stop()
            finally:
                await store.close()

    async def test_custom_embeds_and_media_adapter_routes_have_direct_previews(self):
        s = Session(SimpleNamespace(post=AsyncMock()), 7, 1, "one", return_to=AsyncMock())
        s.row = {
            "revision": "r",
            "draft": embed_defaults() | {"body": "Hello"},
            "deleted": False,
            "message": 0,
        }
        for page in (
            "main",
            "content",
            "formatting",
            "sections",
            "style",
            "header_image",
            "footer_image",
        ):
            with self.subTest(page=page):
                s.page = page
                view = EmbedEditor(s, "Saved.")
                self.assert_editor(view, image=page.endswith("_image"))
                if page.endswith("_image"):
                    modal = EditModal(s, page + "_text")
                    expected = (
                        {"header_title", "header_text_color"}
                        if page.startswith("header")
                        else {"footer_image_text", "footer_text_color"}
                    )
                    self.assertEqual(set(modal.fields), expected)
                preview = next(b for b in buttons(view) if b.label == "Preview")
                await preview.callback(event())
                s.cog.post.assert_awaited()
                self.assertTrue(s.cog.post.await_args.kwargs["private"])
                view.stop()

    async def test_all_shared_menu_targets_use_the_same_image_editors(self):
        i = event()
        s = DesignSession(i, None, None, {})
        # All shared editable menu targets, including checklists and automation.
        for target in s.targets:
            if target == "welcome_image":
                continue
            await s.select([target])
            for page in ("edit", "header_image", "footer_image", "text_colors", "finish"):
                with self.subTest(target=target, page=page):
                    view = DesignEditor(s, page, "Draft updated.")
                    self.assert_editor(view, image=page.endswith("_image"))
                    view.stop()
            for part in ("header", "footer"):
                modal = AppearanceModal(s, part=part, page=part + "_image")
                self.assertIn(part + "_text_color", modal.fields)
                self.assertNotIn(
                    ("footer" if part == "header" else "header") + "_text_color", modal.fields
                )

    async def test_ticket_images_topics_opening_and_section_formats_have_previews(self):
        config = defaults()
        panel, topic = "a" * 32, "b" * 32
        config["panels"][panel] = new_panel("Support") | {"types": [topic]}
        config["types"][topic] = new_type("Support")
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        cog = SimpleNamespace(preview_panel=AsyncMock())
        # Test text and icon footers too; optional content must not crowd out controls.
        for content in ("emblem", "text", "icon"):
            config["panels"][panel]["footer_content"] = content
            for pick in (None, "source", "finish", "content"):
                view = TicketAdmin(
                    cog, parent, "r", config, page="art_footer", selected=panel, pick=pick
                )
                self.assert_editor(view, image=True)
                view.stop()
        for opening in (False, True):
            for page in (
                "appearance",
                "art_header",
                "art_footer",
                "text_before",
                "text_after",
                "text_format_before",
                "text_format_after",
            ):
                for pick in (
                    (None, "source", "finish", "content") if page.startswith("art_") else (None,)
                ):
                    with self.subTest(page=page, opening=opening, pick=pick):
                        view = TicketAdmin(
                            cog,
                            parent,
                            "r",
                            config,
                            page=page,
                            selected=panel,
                            opening=opening,
                            pick=pick,
                        )
                        self.assert_editor(view, image=page.startswith("art_"))
                        view.stop()
        for page in ("type_appearance", "type_lettering"):
            view = TicketAdmin(cog, parent, "r", config, page=page, selected=topic)
            self.assert_editor(view)
            original = copy.deepcopy(config)
            await view.preview(event())
            self.assertEqual(config, original)
            preview_config, token = cog.preview_panel.await_args.args[1:]
            self.assertEqual(preview_config["panels"][token]["types"], [topic])
            sample = PublicPanel(preview_config, token, preview=True)
            sample.to_components()
            sample.stop()
            view.stop()

    async def test_image_presets_are_independent_and_render_without_changing_other_image(self):
        profile = default_profile("askdm")
        original = render_menu(profile, "HELLO")
        changed = render_menu(profile | {"header_finish": "gold"}, "HELLO")
        self.assertNotEqual(original[0], changed[0])
        self.assertEqual(original[1], changed[1])
        s = DesignSession(event(), None, None, {})
        await s.select(["askdm"])
        view = DesignEditor(s, "header_image")
        control = next(c for c in view.walk_children() if isinstance(c, discord.ui.Select))
        control._values = ["gold"]
        with patch.object(s, "show", new=AsyncMock()):
            await control.callback(event())
        self.assertEqual(s.drafts["askdm"]["header_finish"], "gold")
        self.assertIsNone(s.drafts["askdm"]["footer_finish"])
        view.stop()

    async def test_text_finish_preserves_both_images_and_their_inherited_text_colors(self):
        s = DesignSession(event(), None, None, {})
        await s.select(["askdm"])
        s.drafts["askdm"].update(text_color="#18AA77", footer_text="COMMUNITY")
        before = render_menu(s.drafts["askdm"], "HELLO")
        view = DesignEditor(s, "text_colors")
        picker = next(c for c in view.walk_children() if isinstance(c, discord.ui.Select))
        picker._values = ["gold"]
        with patch.object(s, "show", new=AsyncMock()):
            await picker.callback(event())
        self.assertEqual(s.drafts["askdm"]["style"], "gold")
        self.assertEqual(render_menu(s.drafts["askdm"], "HELLO"), before)
        view.stop()

    async def test_welcome_card_has_custom_colors_and_private_preview_without_footer_controls(self):
        service = SimpleNamespace(
            repository=SimpleNamespace(
                get_welcome_artwork=AsyncMock(
                    return_value=SimpleNamespace(title="WELCOME", dim=35, background=None)
                )
            )
        )
        s = DesignSession(event(), service, None, {})
        await s.select(["welcome_image"])
        for page in ("edit", "finish"):
            view = DesignEditor(s, page)
            self.assert_editor(view)
            labels = [b.label for b in buttons(view)]
            self.assertIn("Saved colors" if page == "edit" else "Custom colors", labels)
            self.assertNotIn("FOOTER", str(view.to_components()))
            view.stop()
        modal = AppearanceModal(s)
        self.assertIn("header_text_color", modal.fields)

    async def test_image_saved_colors_only_offer_the_current_image_target(self):
        s = DesignSession(event(), None, None, {})
        await s.select(["askdm"])
        with patch("ky_bot.design.saved_colors.open_library", new=AsyncMock()) as launch:
            await s.open_saved_colors(event(), part="footer")
        self.assertEqual(set(launch.await_args.args[2]), {"footer_text_color"})

    async def test_topic_preview_works_without_any_configured_panel(self):
        config = defaults()
        token = "b" * 32
        config["types"][token] = new_type("Support")
        cog = SimpleNamespace(preview_panel=AsyncMock())
        view = TicketAdmin(
            cog,
            SimpleNamespace(panel_manager=None),
            "r",
            config,
            page="type_lettering",
            selected=token,
        )
        await view.preview(event())
        data, panel_id = cog.preview_panel.await_args.args[1:]
        sample = PublicPanel(data, panel_id, preview=True)
        sample.to_components()
        self.assertEqual(config["panels"], {})
        sample.stop()
        view.stop()
