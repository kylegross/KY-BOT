import io
import unittest

from PIL import Image

from ky_bot.advice.panels import heading_image
from ky_bot.advice.store import defaults as advice_defaults
from ky_bot.checklists.artwork import title_image as checklist_title
from ky_bot.confessions.headings import title_image as confession_title
from ky_bot.tickets.artwork import public_files
from ky_bot.tickets.presentation import heading_file
from ky_bot.tickets.store import defaults, new_panel, new_type
from ky_bot.views.menu_strips import category_files


class CustomHeadingMetalTests(unittest.TestCase):
    def assert_metal(self, raw):
        with Image.open(io.BytesIO(raw)).convert("RGBA") as image:
            colors = {pixel[:3] for pixel in image.get_flattened_data() if pixel[3] > 240}
        self.assertGreater(len(colors), 15)

    def test_confessions_checklists_and_advice_use_metallic_custom_headings(self):
        for renderer in (
            lambda: confession_title("CUSTOM HEADING", "silver_neon", "#335CCC"),
            lambda: checklist_title("CUSTOM HEADING", "silver_neon", "#335CCC"),
            lambda: heading_image("CUSTOM HEADING", advice_defaults() | {"text_color": "#335CCC"}),
        ):
            self.assert_metal(renderer())

    def test_command_center_and_dmfr_category_strips_use_metallic_custom_colors(self):
        for title in ("DESIGN", "ACCESS & PRIVACY", "ROLE PERMISSIONS", "CHANNELS & LOGGING"):
            files = category_files([title], "silver_neon", "#335CCC")
            try:
                self.assert_metal(files[0].fp.getvalue())
            finally:
                for file in files:
                    file.close()

    def test_ticket_topic_and_text_section_custom_colors_are_metallic(self):
        key, topic = "a" * 32, "b" * 32
        config = defaults()
        config["panels"][key] = new_panel("Support") | {
            "types": [topic],
            "before_heading": "CUSTOM SECTION",
            "before_heading_style": "decorative",
            "before_color": "335CCC",
        }
        config["types"][topic] = new_type("Support") | {
            "heading_style": "decorative",
            "heading_color": "335CCC",
        }
        files = public_files(config["panels"][key], config, key)
        try:
            rendered = {file.filename: file.fp.getvalue() for file in files}
            self.assert_metal(rendered[heading_file(topic)])
            self.assert_metal(rendered["ticket_panel_before_heading.png"])
        finally:
            for file in files:
                file.close()
