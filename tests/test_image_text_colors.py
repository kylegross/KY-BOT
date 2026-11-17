import base64
import io
import unittest

from PIL import Image

from ky_bot.confessions.appearance import configured_artwork
from ky_bot.confessions.store import defaults as confession_defaults
from ky_bot.custom_embeds.model import defaults as embed_defaults
from ky_bot.custom_embeds.model import render as render_embed
from ky_bot.design.image_text import add_image_text, metallic_ink
from ky_bot.design.menu_design import default_profile, render_menu
from ky_bot.tickets.artwork import public_files
from ky_bot.tickets.store import new_panel


def png():
    result = io.BytesIO()
    Image.new("RGBA", (1200, 280), (23, 47, 68, 255)).save(result, "PNG")
    return result.getvalue()


def attachments(files):
    try:
        return {file.filename: file.fp.getvalue() for file in files}
    finally:
        for file in files:
            file.close()


class ImageTextColorTests(unittest.TestCase):
    def test_custom_text_color_retains_metallic_shading_and_alpha(self):
        mask = Image.new("L", (180, 60), 210)
        ink = metallic_ink(mask, "silver_neon", "#7B2CBF")
        self.assertEqual(set(ink.getchannel("A").get_flattened_data()), {210})
        self.assertGreater(len(set(ink.convert("RGB").get_flattened_data())), 30)

    def test_uploaded_picture_keeps_its_background_when_text_is_added(self):
        source = png()
        for kind in ("header", "footer"):
            rendered = add_image_text(source, "MY TEXT", kind, "gold", "#F790C8")
            self.assertNotEqual(rendered, source)
            with Image.open(io.BytesIO(rendered)) as image:
                self.assertEqual(image.size, (1200, 280))
                self.assertEqual(image.getpixel((0, 0)), (23, 47, 68, 255))
        self.assertEqual(add_image_text(source, "", "footer"), source)

    def test_confession_and_advice_compatible_artwork_supports_uploaded_text_colors(self):
        encoded = base64.b64encode(png()).decode()
        config = confession_defaults() | dict(
            header=encoded,
            footer=encoded,
            header_title="MY HEADER",
            header_title_on_upload=True,
            header_text_color="#F790C8",
            footer_image_text="MY FOOTER",
            footer_text_color="#66D4AD",
        )
        for kind in ("header", "footer"):
            rendered = configured_artwork(config, kind)
            self.assertNotEqual(rendered, png())
            with Image.open(io.BytesIO(rendered)) as image:
                self.assertEqual(image.getpixel((0, 0)), (23, 47, 68, 255))
        config.update(header_title_on_upload=False, footer_image_text="")
        self.assertEqual(configured_artwork(config, "header"), png())
        self.assertEqual(configured_artwork(config, "footer"), png())

    def test_shared_menu_header_and_footer_text_colors_are_independent(self):
        encoded = base64.b64encode(png()).decode()
        profile = default_profile("settings:overview") | dict(
            header_background=encoded,
            footer_background=encoded,
            title="HEADER",
            footer_text="FOOTER",
            header_text_color="#FF55AA",
            footer_text_color="#66D4AD",
        )
        header, footer = render_menu(profile, "TITLE")
        new_header, same_footer = render_menu(profile | {"header_text_color": "#44BBFF"}, "TITLE")
        same_header, new_footer = render_menu(profile | {"footer_text_color": "#44BBFF"}, "TITLE")
        self.assertNotEqual(header, new_header)
        self.assertEqual(footer, same_footer)
        self.assertEqual(header, same_header)
        self.assertNotEqual(footer, new_footer)

    def test_manage_embeds_uploaded_header_footer_have_independent_metallic_text(self):
        encoded = base64.b64encode(png()).decode()
        config = embed_defaults() | dict(
            header=encoded,
            footer=encoded,
            header_title="HEADER",
            footer_image_text="FOOTER",
            header_text_color="#FF55AA",
            footer_text_color="#66D4AD",
        )
        view, files = render_embed(config)
        original = attachments(files)
        view.stop()
        view, files = render_embed(config | {"header_text_color": "#44BBFF"})
        changed = attachments(files)
        view.stop()
        self.assertNotEqual(original["custom_header.png"], changed["custom_header.png"])
        self.assertEqual(original["custom_footer.png"], changed["custom_footer.png"])

    def test_ticket_uploaded_header_footer_have_independent_text_colors(self):
        encoded = base64.b64encode(png()).decode()
        panel = new_panel("Support") | dict(
            header_mode="custom",
            footer_mode="custom",
            header_custom=encoded,
            footer_custom=encoded,
            header_title="HEADER",
            footer_content="text",
            footer_banner_text="FOOTER",
            header_text_color="#FF55AA",
            footer_text_color="#66D4AD",
        )
        original = attachments(public_files(panel))
        changed = attachments(public_files(panel | {"header_text_color": "#44BBFF"}))
        self.assertNotEqual(original["ticket_panel_header.png"], changed["ticket_panel_header.png"])
        self.assertEqual(original["ticket_panel_footer.png"], changed["ticket_panel_footer.png"])
