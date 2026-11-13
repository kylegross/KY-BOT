import io
import unittest
from importlib.resources import files

from PIL import Image, ImageChops

from ky_bot.checklists.artwork import title_image
from ky_bot.design.typography import section_heading_mask
from ky_bot.views.menu_strips import GROUPS, strip_files


class SilverHeadingSpacingTests(unittest.TestCase):
    def test_shared_custom_embed_heading_retains_glyph_size_with_six_pixel_inset(self):
        mask = section_heading_mask("SAVED EMBEDS")
        with Image.open(io.BytesIO(title_image("SAVED EMBEDS", "silver_neon"))) as image:
            alpha = image.getchannel("A")
            self.assertEqual(image.size, (1120, mask.height + 32))
            self.assertIsNone(alpha.crop((0, 0, 6, image.height)).getbbox())
            rendered = alpha.crop((6, 16, 6 + mask.width, 16 + mask.height))
            self.assertIsNone(ImageChops.difference(rendered, mask).getbbox())

    def test_all_packaged_categories_keep_the_left_inset_and_canvas_size(self):
        for path in files("ky_bot.design").joinpath("assets", "strips").iterdir():
            if path.name.endswith(".png"):
                with (
                    self.subTest(path=path.name),
                    path.open("rb") as stream,
                    Image.open(stream) as image,
                ):
                    self.assertEqual(image.width, 1120)
                    self.assertGreaterEqual(image.getchannel("A").getbbox()[0], 6)

    def test_every_menu_group_has_packaged_iridescent_category_artwork(self):
        for menu in GROUPS:
            with self.subTest(menu=menu):
                attachments = strip_files(menu, "silver_neon")
                try:
                    self.assertEqual(len(attachments), len(GROUPS[menu]))
                finally:
                    for attachment in attachments:
                        attachment.close()
