import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from ky_bot.advice.menus import Admin
from ky_bot.advice.panels import artwork_bytes
from ky_bot.advice.store import Store


class AppearanceWorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "advice.sqlite3"
        self.store = await Store.open(self.path)
        self.revision, self.config = await self.store.configure(1, 10, None)
        self.parent = SimpleNamespace(
            panel_manager=object(),
            interaction_check=AsyncMock(side_effect=lambda i: not i.message.flags.ephemeral),
        )
        self.guild = SimpleNamespace(
            fetch_member=AsyncMock(
                return_value=SimpleNamespace(
                    guild_permissions=discord.Permissions(administrator=True)
                )
            )
        )
        self.i = self.interaction(False)
        self.menu = Admin(SimpleNamespace(store=self.store), 7, 1, parent=self.parent)
        self.menu.channel, self.menu.revision, self.menu.config = 10, self.revision, self.config
        for name in ("standard_artwork", "heading_image"):
            patcher = patch("ky_bot.advice.panels." + name, return_value=b"art")
            patcher.start()
            self.addCleanup(patcher.stop)

    def interaction(self, private):
        return SimpleNamespace(
            user=SimpleNamespace(id=7),
            guild_id=1,
            guild=self.guild,
            message=SimpleNamespace(flags=SimpleNamespace(ephemeral=private)),
            response=SimpleNamespace(send_modal=AsyncMock(), defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    async def asyncTearDown(self):
        self.menu.stop()
        await self.store.close()
        self.directory.cleanup()

    async def test_private_preview_from_shared_command_center_saves_and_survives_restart(self):
        await self.menu.open_palette(self.i, "header")
        modal = self.i.response.send_modal.await_args.args[0]
        private = self.interaction(True)
        self.assertTrue(await modal.allowed(private))
        palette = {"base": "#7B2CBF", "highlight": "#FFDDFF", "shadow": None, "accent": None}
        with patch("ky_bot.advice.menus.display_menu", new=AsyncMock()) as display:
            await modal.commit(private, palette)
        self.assertEqual((await self.store.config(1, 10))[1]["header_palette"], palette)
        self.parent.interaction_check.assert_awaited_once()
        saved_menu = display.await_args.args[1]
        self.assertIsNone(saved_menu.parent)
        self.assertEqual(saved_menu.page, "header_artwork")
        self.assertIn("Custom colors", str(saved_menu.to_components()))
        saved_menu.stop()
        await self.store.close()
        self.store = await Store.open(self.path)
        self.assertEqual((await self.store.config(1, 10))[1]["header_palette"], palette)

    async def test_private_preview_still_rejects_other_users(self):
        await self.menu.open_palette(self.i, "header")
        modal = self.i.response.send_modal.await_args.args[0]
        private = self.interaction(True)
        private.user.id = 8
        with self.assertRaisesRegex(ValueError, "own"):
            await modal.allowed(private)

    async def test_saving_colors_does_not_require_unrelated_delivery_permissions(self):
        revision, config = await self.store.configure(
            1,
            10,
            self.revision,
            enabled=True,
            access_channel=20,
            access_role=30,
            advice_role=40,
        )
        self.menu.revision, self.menu.config = revision, config
        self.menu.cog.validate_setup = AsyncMock(
            side_effect=ValueError("Missing delivery permissions")
        )
        with patch("ky_bot.advice.menus.destination") as destination:
            await self.menu.save(self.i, revision, header_palette={"base": "#7B2CBF"})
        self.menu.cog.validate_setup.assert_not_awaited()
        destination.assert_not_called()
        self.assertEqual((await self.store.config(1, 10))[1]["header_palette"]["base"], "#7B2CBF")

    async def test_header_preset_does_not_clear_footer_colors_or_upload(self):
        footer = base64.b64encode(b"uploaded footer").decode()
        self.revision, self.config = await self.store.configure(
            1,
            10,
            self.revision,
            header_palette={"base": "#AA22CC"},
            footer=footer,
            footer_palette={"base": "#22CC88"},
        )
        with patch("ky_bot.advice.menus.display_menu", new=AsyncMock()):
            await self.menu.show(self.i, "header_artwork")
            picker = next(
                item for item in self.menu.walk_children() if isinstance(item, discord.ui.Select)
            )
            picker._values = ["gold"]
            await picker.callback(self.i)
        _, config = await self.store.config(1, 10)
        self.assertEqual(config["header_artwork_finish"], "gold")
        self.assertIsNone(config["header_palette"])
        self.assertEqual(config["footer"], footer)
        self.assertEqual(config["footer_palette"]["base"], "#22CC88")

    async def test_editor_stays_neutral_and_preview_uses_saved_colors(self):
        self.revision, _ = await self.store.configure(
            1,
            10,
            self.revision,
            header_palette={"base": "#7B2CBF"},
            color="22AA66",
        )
        with (
            patch("ky_bot.advice.menus.display_menu", new=AsyncMock()),
            patch("ky_bot.advice.panels.standard_artwork", return_value=b"art") as render,
        ):
            await self.menu.show(self.i, "header_artwork")
            self.assertTrue(all(call.args[5] is None for call in render.call_args_list))
            self.assertEqual(self.menu.children[0].accent_colour, 0xD6E2F3)
            render.reset_mock()
            preview = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
            await self.menu.preview(preview, True)
            self.assertTrue(
                any(
                    call.args[0] == "header" and call.args[5] and call.args[5][0] == "#7B2CBF"
                    for call in render.call_args_list
                )
            )

    async def test_title_color_uses_artwork_instead_of_unrelated_panel_heading_color(self):
        config = self.config | {"text_color": "#00FF00", "header_artwork_finish": "gold"}
        with patch("ky_bot.advice.panels.standard_artwork", return_value=b"art") as render:
            artwork_bytes(config, "header")
        self.assertEqual(render.call_args.args[1], "gold")
        self.assertEqual(render.call_args.args[3], "gold")
        self.assertIsNone(render.call_args.args[4])

    async def test_confession_color_preview_can_save_from_a_shared_command_center(self):
        from ky_bot.confessions.menus import Admin as ConfessionAdmin
        from ky_bot.confessions.store import Store as ConfessionStore

        store = await ConfessionStore.open(":memory:")
        try:
            await store.configure(1, 10, None)
            menu = ConfessionAdmin(SimpleNamespace(store=store), 7, 1, parent=self.parent)
            menu.channel = 10
            with patch("ky_bot.confessions.menus.display_menu", new=AsyncMock()):
                await menu.show(self.i, "artwork")
            section = next(
                item
                for item in menu.walk_children()
                if isinstance(item, discord.ui.Section)
                and "**CUSTOM HEADER COLORS**" in item.children[0].content
            )
            await section.accessory.callback(self.i)
            modal = self.i.response.send_modal.await_args.args[0]
            calls = self.parent.interaction_check.await_count
            private = self.interaction(True)
            self.assertTrue(await modal.allowed(private))
            with (
                patch("ky_bot.confessions.menus.destination"),
                patch.object(ConfessionAdmin, "show", new=AsyncMock()),
            ):
                await modal.commit(private, {"base": "#7B2CBF"})
            self.assertEqual((await store.config(1, 10))[1]["header_palette"]["base"], "#7B2CBF")
            self.assertEqual(self.parent.interaction_check.await_count, calls)
            menu.stop()
        finally:
            await store.close()

    async def test_uploaded_image_text_controls_fit_with_all_options_enabled(self):
        import io

        from PIL import Image

        from ky_bot.confessions.menus import Admin as ConfessionAdmin
        from ky_bot.confessions.store import Store as ConfessionStore

        output = io.BytesIO()
        Image.new("RGBA", (1200, 280), "#172F44").save(output, "PNG")
        encoded = base64.b64encode(output.getvalue()).decode()
        values = dict(
            header=encoded,
            footer=encoded,
            header_title_on_upload=True,
            footer_image_text="Our community",
            header_text_color="#FF77AA",
            footer_text_color="#66CCAA",
        )
        self.revision, _ = await self.store.configure(1, 10, self.revision, **values)
        with patch("ky_bot.advice.menus.display_menu", new=AsyncMock()):
            for page in ("artwork", "header_artwork", "footer_artwork"):
                await self.menu.show(self.i, page, "Saved.")
                self.assertLessEqual(self.menu.total_children_count, 40, page)
        store = await ConfessionStore.open(":memory:")
        try:
            await store.configure(1, 10, None, **values)
            menu = ConfessionAdmin(SimpleNamespace(store=store), 7, 1, parent=self.parent)
            menu.channel = 10
            with patch("ky_bot.confessions.menus.display_menu", new=AsyncMock()):
                for page in ("artwork", "image_text"):
                    await menu.show(self.i, page, "Saved.")
                    self.assertLessEqual(menu.total_children_count, 40, page)
            menu.stop()
        finally:
            await store.close()

    async def test_section_colors_stay_independent_and_all_lines_updates_overrides(self):
        self.menu.page = "section"
        self.menu.section = 0
        with patch("ky_bot.design.saved_colors.open_library", new=AsyncMock()) as launch:
            await self.menu.open_saved_colors(self.i)
        first = launch.await_args.args[3]
        await first(self.i, {}, "text_color", "#18AA77")
        self.menu.section = 1
        with patch("ky_bot.design.saved_colors.open_library", new=AsyncMock()) as launch:
            await self.menu.open_saved_colors(self.i)
        await launch.await_args.args[3](self.i, {}, "text_color", "#EE7799")
        await self.menu.apply_heading_style(self.i, {"line_color": "#FF0088"}, section=1)
        await self.menu.apply_heading_style(self.i, {"line_color": "#18AA77"})
        _, config = await self.store.config(1, 10)
        self.assertEqual(config["section_styles"]["0"]["text_color"], "#18AA77")
        self.assertEqual(config["section_styles"]["1"]["text_color"], "#EE7799")
        for style in config["section_styles"].values():
            self.assertEqual(style["line_color"], "#18AA77")
        reopened = await Store.open(self.path)
        try:
            self.assertEqual((await reopened.config(1, 10))[1], config)
        finally:
            await reopened.close()

    async def test_optional_heading_is_rendered_above_its_button_row(self):
        from ky_bot.advice.panels import access_panel

        config = self.config
        config["sections"][0]["rows"][0]["heading"] = "JOIN THE CONVERSATION"
        self.revision, config = await self.store.configure(1, 10, self.revision, **config)
        with patch("ky_bot.advice.panels.heading_image", return_value=b"heading") as heading:
            view = access_panel(10, config, preview=True)
        self.assertIn("JOIN THE CONVERSATION", [call.args[0] for call in heading.call_args_list])
        items = view.panel.children
        button_row = next(n for n, item in enumerate(items) if isinstance(item, discord.ui.Section))
        self.assertIsInstance(items[button_row - 1], discord.ui.MediaGallery)
        self.assertLessEqual(view.total_children_count, 40)
        view.stop()

    async def test_section_color_controls_fit_discord_limits(self):
        self.revision, _ = await self.store.configure(
            1,
            10,
            self.revision,
            heading_colors={"Green": "#18AA77"},
            section_styles={"0": {"text_finish": "silver_neon", "text_color": "#18AA77"}},
        )
        with patch("ky_bot.advice.menus.display_menu", new=AsyncMock()):
            await self.menu.show(self.i, "section")
            self.assertLessEqual(self.menu.total_children_count, 40)
