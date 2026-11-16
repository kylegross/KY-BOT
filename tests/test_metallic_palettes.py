import base64
import io
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import AsyncMock

from PIL import Image

from ky_bot.custom_embeds.artwork import custom_image
from ky_bot.custom_embeds.model import defaults as embed_defaults
from ky_bot.custom_embeds.model import validate as validate_embed
from ky_bot.design.menu_design import default_profile, render_menu
from ky_bot.design.metallic import (
    active_palette,
    parse_palette,
    resolved_palette,
    tint,
    using_palette,
)
from ky_bot.design.palette_editor import PaletteReview
from ky_bot.tickets.artwork import public_files
from ky_bot.tickets.presentation import published_layout
from ky_bot.tickets.store import defaults, new_panel


class MetallicPaletteTests(unittest.IsolatedAsyncioTestCase):
    def test_hex_validation_derived_colors_and_custom_overrides(self):
        raw = parse_palette({"base": "7b2cbf"})
        self.assertEqual(raw["base"], "#7B2CBF")
        palette = resolved_palette(raw)
        self.assertEqual(palette["base"], (123, 44, 191))
        self.assertLess(sum(palette["shadow"]), sum(palette["base"]))
        self.assertGreater(sum(palette["highlight"]), sum(palette["base"]))
        self.assertNotEqual(palette["accent"], palette["base"])
        self.assertEqual(
            resolved_palette({"base": "123456", "highlight": "ABCDEF"})["highlight"],
            (171, 205, 239),
        )
        for value in ({"base": "red"}, {"base": "#123"}, {"base": "123456", "shadow": "oops"}):
            with self.assertRaises(ValueError):
                parse_palette(value)

    def test_existing_presets_retain_original_graphic_pixels(self):
        # Captured from all four original finishes before installing custom palettes.
        # Omit dynamic title text so this check does not depend on installed fonts.
        import hashlib

        expected = {
            "gold": (
                "a107ef3ea914f30298fe29bdc4f38495d2ceee3a6f262aa67a7545f1e9c68569",
                "f06d9694b783fe18a4d07ef65b960e15935d43bda987c2cd431dfa1954b3e847",
            ),
            "dark_silver": (
                "66da2292b4d38e42a89bcf734ad7e864ea5a1c89ecba897d2c69b1826d0d8741",
                "1adcf0b816a3cf118d63a7242947549d7221c561c1b3464665fba4beff5106a8",
            ),
            "silver_neon": (
                "1105dadf42f143cd478ecb6b0aafa29840681704d4c9d2fb00101d36ec4cd09d",
                "a12adc15a99086358e66e21bf589f8b7820fabba707eaf09bfd4cccbde752c0e",
            ),
            "red_iridescent": (
                "f26691d33c20dfa68f99961286e59eb398571555223b1df289f6a6565d54ce1b",
                "94db1813b6a672decff3f7d4b83464463662818a3570ee4bcab6fd7d4ed8ebc0",
            ),
        }
        for style, original in expected.items():
            images = render_menu(default_profile("tickets") | dict(style=style), None)
            actual = tuple(
                hashlib.sha256(Image.open(io.BytesIO(raw)).convert("RGBA").tobytes()).hexdigest()
                for raw in images
            )
            self.assertEqual(actual, original)

    def test_header_footer_palettes_are_independent_and_keep_detail(self):
        profile = default_profile("tickets")
        original = render_menu(profile, "CUSTOM COLORS")
        header = render_menu(profile | dict(header_palette={"base": "7B2CBF"}), "CUSTOM COLORS")
        footer = render_menu(profile | dict(footer_palette={"base": "39DCCB"}), "CUSTOM COLORS")
        self.assertNotEqual(header[0], original[0])
        self.assertEqual(header[1], original[1])
        self.assertEqual(footer[0], original[0])
        self.assertNotEqual(footer[1], original[1])
        image = Image.open(io.BytesIO(header[0])).convert("RGB")
        self.assertGreater(len(image.getcolors(image.width * image.height)), 1000)

    def test_alpha_context_cleanup_and_parallel_renders(self):
        source = Image.new("RGBA", (12, 4))
        source.putdata([(n * 5, n * 5, n * 5, n * 5) for n in range(48)])
        with using_palette({"base": "7B2CBF"}):
            recolored = tint(source)
            self.assertEqual(source.getchannel("A").tobytes(), recolored.getchannel("A").tobytes())
        self.assertIsNone(active_palette())
        with self.assertRaises(RuntimeError):
            with using_palette({"base": "39DCCB"}):
                raise RuntimeError()
        self.assertIsNone(active_palette())

        def render(color):
            return render_menu(
                default_profile("tickets") | dict(header_palette={"base": color}), "PARALLEL"
            )[0]

        render_menu(default_profile("tickets"), "PREWARM")
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(render, ["FF0000", "0000FF"]))
        self.assertNotEqual(results[0], results[1])
        self.assertIsNone(active_palette())

    def test_uploaded_footer_icon_keeps_its_own_pixels(self):
        icon = Image.new("RGBA", (80, 80))
        icon.paste("#FF7F00", (10, 10, 70, 70))
        stream = io.BytesIO()
        icon.save(stream, format="PNG")
        profile = default_profile("tickets") | dict(
            footer_icon=base64.b64encode(stream.getvalue()).decode(),
            footer_palette={"base": "7B2CBF"},
        )
        footer = Image.open(io.BytesIO(render_menu(profile, "TEST")[1])).convert("RGB")
        self.assertEqual(footer.getpixel((800, 75)), (255, 127, 0))

    def test_ticket_palette_snapshots_and_custom_embed_images(self):
        config = defaults()
        config["panels"]["a" * 32] = new_panel("TEST")
        panel = config["panels"]["a" * 32]
        panel["header_palette"] = parse_palette({"base": "7B2CBF"})
        snapshot = published_layout(config, "a" * 32)
        panel["header_palette"]["base"] = "#FF0000"
        self.assertEqual(snapshot["panel"]["header_palette"]["base"], "#7B2CBF")
        files = public_files(panel)
        try:
            self.assertEqual(len(files), 2)
        finally:
            for file in files:
                file.close()
        data = embed_defaults() | dict(
            header=custom_image("header", {"base": "7B2CBF"}), header_palette={"base": "7B2CBF"}
        )
        validated = validate_embed(data)
        self.assertEqual(validated["header_palette"]["base"], "#7B2CBF")

    async def test_preview_does_not_save_until_explicit_use_and_discard_never_saves(self):
        commit = AsyncMock()
        allowed = AsyncMock(return_value=True)
        view = PaletteReview(parse_palette({"base": "7B2CBF"}), [], commit, allowed)
        commit.assert_not_awaited()
        event = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await view.discard(event)
        commit.assert_not_awaited()
        view = PaletteReview(parse_palette({"base": "39DCCB"}), [], commit, allowed)
        await view.use(event)
        self.assertEqual(commit.await_args.args[1]["base"], "#39DCCB")

    async def test_ticket_color_modal_previews_before_saving_and_keeps_other_part(self):
        from ky_bot.tickets.views import Admin

        config = defaults()
        config["panels"]["a" * 32] = new_panel("Color test")
        cog = SimpleNamespace(store=SimpleNamespace(configure=AsyncMock()), admin=AsyncMock())
        parent = SimpleNamespace(
            panel_manager=None, guild_id=1, interaction_check=AsyncMock(return_value=True)
        )
        menu = Admin(cog, parent, "revision", config, page="art_header", selected="a" * 32)
        launch = next(
            c for c in menu.walk_children() if getattr(c, "label", None) == "Custom colors"
        )
        event = SimpleNamespace(
            response=SimpleNamespace(
                send_modal=AsyncMock(), defer=AsyncMock(), is_done=lambda: True
            ),
            edit_original_response=AsyncMock(),
        )
        await launch.callback(event)
        modal = event.response.send_modal.await_args.args[0]
        modal.fields["base"]._value = "#7B2CBF"
        await modal.on_submit(event)
        cog.store.configure.assert_not_awaited()
        review = event.edit_original_response.await_args.kwargs["view"]
        await review.use(event)
        saved = cog.store.configure.await_args.args[2]["panels"]["a" * 32]
        self.assertEqual(saved["header_palette"]["base"], "#7B2CBF")
        self.assertIsNone(saved["footer_palette"])
        menu.stop()

    async def test_design_editor_header_and_footer_custom_controls_keep_parts_separate(self):
        from unittest.mock import Mock, patch

        from ky_bot.design.editor import DesignSession, Editor

        event = SimpleNamespace(
            user=SimpleNamespace(id=50),
            guild_id=1,
            guild=SimpleNamespace(),
            client=SimpleNamespace(),
            permissions=SimpleNamespace(administrator=True),
            response=SimpleNamespace(send_modal=AsyncMock(), defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        session = DesignSession(event, Mock(), None, {})
        await session.select(["settings:general"])
        menu = Editor(session, "finish")
        for part in ("header", "footer"):
            section = next(
                c
                for c in menu.walk_children()
                if hasattr(c, "children")
                and any(
                    getattr(t, "content", "").startswith("**CUSTOM " + part.upper() + " COLORS**")
                    for t in c.children
                )
            )
            await section.accessory.callback(event)
            modal = event.response.send_modal.await_args.args[0]
            modal.fields["base"]._value = "#168C91"
            await modal.on_submit(event)
            self.assertIsNone(session.drafts["settings:general"].get(part + "_palette"))
            review = event.edit_original_response.await_args.kwargs["view"]
            with patch.object(session, "show", new=AsyncMock()):
                await review.use(event)
            self.assertEqual(
                session.drafts["settings:general"][part + "_palette"]["base"], "#168C91"
            )
        menu.stop()

    def test_confession_custom_palette_changes_only_selected_standard_banner(self):
        from ky_bot.confessions.cards import uploads
        from ky_bot.confessions.store import (
            defaults as confession_defaults,
        )
        from ky_bot.confessions.store import (
            validate as validate_confession,
        )

        config = confession_defaults()
        originals = uploads(config)
        try:
            baseline = {f.filename: f.fp.read() for f in originals}
        finally:
            for file in originals:
                file.close()
        config["header_palette"] = parse_palette({"base": "7B2CBF"})
        validate_confession(config)
        files = uploads(config)
        try:
            rendered = {f.filename: f.fp.read() for f in files}
        finally:
            for file in files:
                file.close()
        self.assertNotEqual(rendered["confession_header.png"], baseline["confession_header.png"])
        self.assertEqual(rendered["confession_footer.png"], baseline["confession_footer.png"])

    def test_polished_reflections_have_depth_without_brightening_dark_backgrounds(self):
        source = Image.new("RGBA", (128, 128), (155, 155, 155, 255))
        with using_palette({"base": "7B2CBF"}):
            polished = tint(source).convert("RGB")
            background = tint(Image.new("RGBA", (128, 128), (25, 25, 25, 255)))
        reflection = sum(polished.getpixel((64, 34)))
        lowlight = sum(polished.getpixel((64, 59)))
        self.assertGreater(reflection - lowlight, 100)
        self.assertTrue(
            all(low == high and high < 25 for low, high in background.convert("RGB").getextrema())
        )
        with using_palette({"base": "7B2CBF", "accent": "00CCFF"}):
            blue_reflection = tint(source)
        with using_palette({"base": "7B2CBF", "accent": "FF4477"}):
            pink_reflection = tint(source)
        self.assertNotEqual(blue_reflection.tobytes(), pink_reflection.tobytes())

    async def test_saved_custom_embed_rebuilds_palette_graphics_with_current_finish(self):
        from ky_bot.custom_embeds.artwork import standard_image
        from ky_bot.custom_embeds.model import render

        palette = parse_palette({"base": "7B2CBF"})
        data = embed_defaults() | dict(
            header=standard_image("header", "dark_silver"), header_palette=palette
        )
        view, files = render(data)
        try:
            header = next(file for file in files if file.filename == "custom_header.png")
            self.assertEqual(header.fp.read(), base64.b64decode(custom_image("header", palette)))
        finally:
            for file in files:
                file.close()
            view.stop()
