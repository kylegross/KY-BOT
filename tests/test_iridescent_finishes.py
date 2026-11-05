import io
import unittest

from PIL import Image, ImageChops, ImageDraw

from ky_bot.checklists.artwork import title_image
from ky_bot.design.footers import STYLES, metal, text_ink
from ky_bot.design.typography import section_heading_mask


class IridescentFinishTests(unittest.TestCase):
    def mask(self):
        mask = Image.new("L", (180, 36))
        ImageDraw.Draw(mask).rectangle((3, 3, 176, 32), fill=255)
        return mask

    def test_all_finishes_vary_in_both_directions_and_preserve_alpha(self):
        mask = self.mask()
        for style in STYLES:
            with self.subTest(style=style):
                ink = text_ink(mask, style)
                self.assertEqual(ink.size, mask.size)
                self.assertIsNone(ImageChops.difference(ink.getchannel("A"), mask).getbbox())
                self.assertNotEqual(ink.getpixel((20, 18)), ink.getpixel((130, 18)))
                self.assertNotEqual(ink.getpixel((90, 5)), ink.getpixel((90, 20)))

    def test_dark_silver_is_readable_and_darker_than_multicolor_silver(self):
        mask = self.mask()

        def brightness(style):
            image = metal(mask, style).convert("RGB")
            return sum(
                sum(image.getpixel((x, y))) / 3 for y in range(3, 33) for x in range(3, 177)
            ) / (30 * 174)

        self.assertGreater(brightness("dark_silver"), 130)
        self.assertLess(brightness("dark_silver"), brightness("silver_neon"))

    def test_explicit_custom_colors_override_every_finish_exactly(self):
        mask = self.mask()
        for style in STYLES:
            with self.subTest(style=style):
                ink = text_ink(mask, style, "#39DCCB")
                self.assertEqual(ink.getpixel((90, 18)), (57, 220, 203, 255))
                self.assertIsNone(ImageChops.difference(ink.getchannel("A"), mask).getbbox())

    def test_dynamic_headings_share_finishes_without_changing_inset_or_glyphs(self):
        mask = section_heading_mask("SAVED EMBEDS")
        for style in STYLES:
            with self.subTest(style=style):
                with Image.open(io.BytesIO(title_image("SAVED EMBEDS", style))) as image:
                    crop = image.crop((3, 16, 3 + mask.width, 16 + mask.height))
                    expected = text_ink(mask, style)
                    self.assertIsNone(ImageChops.difference(crop, expected).getbbox())
                    self.assertIsNone(image.getchannel("A").crop((0, 0, 3, image.height)).getbbox())

    def test_custom_menu_colors_preserve_packaged_rule_and_heading_geometry(self):
        from ky_bot.views.menu_strips import GROUPS, strip_files

        for menu in GROUPS:
            for style in STYLES:
                with self.subTest(menu=menu, style=style):
                    standard = strip_files(menu, style)
                    custom = strip_files(menu, style, "#39DCCB")
                    try:
                        for before, after in zip(standard, custom):
                            self.assertEqual(before.filename, after.filename)
                            with Image.open(before.fp) as original, Image.open(after.fp) as tinted:
                                self.assertEqual(original.size, tinted.size)
                                self.assertIsNone(
                                    ImageChops.difference(
                                        original.getchannel("A"), tinted.getchannel("A")
                                    ).getbbox()
                                )
                    finally:
                        for attachment in standard + custom:
                            attachment.close()
