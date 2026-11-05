import io
import unittest
from importlib.resources import files
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from ky_bot.admin_functions.settings_repository import WelcomeArtwork
from ky_bot.design.typography import TypographyError
from ky_bot.design.unicode_text import (
    choose_font,
    emoji_bitmap,
    first_grapheme,
    fit_text,
    font_info,
    runs,
    text_image,
    wrap_text,
)
from ky_bot.design.welcome import font, render_welcome
from ky_bot.leveling.artwork import level_card


class UnicodeNameTests(unittest.TestCase):
    def setUp(self):
        self.primary = str(font("display", 70).path)
        self.bundled = tuple(
            str(path)
            for path in Path(str(files("ky_bot.design").joinpath("assets", "fonts"))).glob("*.ttf")
        )

    def test_athena_keeps_primary_letters_and_uses_color_emoji_for_sparkles(self):
        parts = runs("Athena✨", self.primary)
        self.assertEqual(parts[0], (self.primary, "Athena"))
        self.assertEqual(Path(parts[1][0]).name, "NotoColorEmoji.ttf")
        self.assertEqual(parts[1][1], "✨")
        tile = text_image("Athena✨", 70, self.primary, "#D6E2F3", metallic="silver_neon")
        self.assertTrue(
            any(r > 170 and g > 100 and b < 100 and a > 180 for r, g, b, a in tile.getdata())
        )

    def test_joined_emoji_flags_and_keycaps_render_as_single_color_glyphs(self):
        for value in ("✨", "👩🏽‍💻", "🇺🇸", "1️⃣", "👨‍👩‍👧‍👦"):
            self.assertEqual(first_grapheme(value + " Name"), value)
            parts = runs(value, self.primary)
            self.assertEqual(len(parts), 1)
            bitmap = emoji_bitmap(parts[0][0], value, 70)
            self.assertIsNotNone(bitmap, value)
            self.assertGreater(bitmap[0].width, 1)
            with patch(
                "ky_bot.design.unicode_text.face_at",
                side_effect=AssertionError(
                    "Color emoji must use the shaping/bitmap path, not Pillow's font shaper"
                ),
            ):
                self.assertGreater(text_image(value, 70, self.primary, "#FFFFFF").height, 1)

    def test_special_letters_are_covered_by_shipped_fonts_without_system_fonts(self):
        choose_font.cache_clear()
        with patch("ky_bot.design.unicode_text.fallback_paths", return_value=self.bundled):
            for value in ("𝘼𝙩𝙝𝙚𝙣𝙖", "ᐊᓂ", "ΩЖ", "♡", "é", "e\u0301"):
                for path, text in runs(value, self.primary):
                    required = {ord(c) for c in text if c not in ("\u200d", "\ufe0f", "\ufe0e")}
                    self.assertTrue(required <= font_info(path)[0], value)
                self.assertGreater(text_image(value, 70, self.primary, "#FFFFFF").height, 1)
        choose_font.cache_clear()

    def test_fitting_preserves_complete_name_and_wrap_does_not_split_graphemes(self):
        text = "👩🏽‍💻 Athena✨ " * 15
        tile = fit_text(text, 100, self.primary, "#FFFFFF", 820)
        self.assertLessEqual(tile.width, 820)
        self.assertGreater(tile.height, 1)
        lines = wrap_text("👩🏽‍💻" * 20, 30, self.primary, 100, max_lines=20)
        self.assertEqual("".join(lines), "👩🏽‍💻" * 20)
        self.assertTrue(all(not line.startswith("\u200d") for line in lines))

    def test_cards_render_unicode_names_and_colored_emoji(self):
        for value in ("Athena✨", "𝘼𝙩𝙝𝙚𝙣𝙖 ♡", "👩🏽‍💻 ᐊᓂ", "e\u0301 🇺🇸"):
            data = level_card(value, 2, "Nov 05, 2026")
            with Image.open(io.BytesIO(data)) as image:
                self.assertEqual(image.width, 1440)
                crop = image.convert("RGBA").crop(
                    (420, image.height // 2 + 20, 1260, image.height - 15)
                )
                self.assertGreater(crop.getbbox()[2], 1)
        with Image.open(io.BytesIO(level_card("Athena✨", 2, "Nov 05, 2026"))) as image:
            crop = image.convert("RGBA").crop(
                (420, image.height // 2 + 20, 1260, image.height - 15)
            )
            self.assertTrue(
                any(r > 170 and g > 100 and b < 100 and a > 180 for r, g, b, a in crop.getdata())
            )
        data = render_welcome(
            WelcomeArtwork(),
            member_name="Athena✨",
            server_name="Community 🇺🇸",
            message="Welcome 👩🏽‍💻 Athena✨",
            avatar=None,
        )
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.size, (1200, 800))

    def test_missing_licensed_font_uses_packaged_unicode_font(self):
        with patch("ky_bot.design.welcome.font_file", side_effect=TypographyError("missing")):
            face = font("display", 70)
            self.assertEqual(Path(face.path).name, "NotoSans-Regular.ttf")
            tile = fit_text("Athena✨", 70, str(face.path), "#FFFFFF", 820)
            self.assertGreater(tile.height, 1)
