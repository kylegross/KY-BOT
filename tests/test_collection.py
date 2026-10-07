import io
import zipfile
from importlib.resources import files
from pathlib import Path

import pytest
from PIL import Image

from ky_bot.services.collection import COLOURS, floral_background
from ky_bot.services.footers import centered_overlay, render_footer
from ky_bot.services.headers import DESIGNS, design_file, overlay, render_header


@pytest.mark.parametrize("style", DESIGNS)
def test_collection_and_downloads_only_offer_new_finishes(style):
    assert set(DESIGNS) == set(COLOURS)
    assert Image.open(io.BytesIO(design_file(style, compact=True))).size == (1600, 280)
    directory = files("ky_bot").joinpath("assets", "header")
    assert not any(
        "volcanic" in path.name or "floral_wreath" in path.name for path in directory.iterdir()
    )
    root = Path(__file__).resolve().parents[1]
    for archive in [
        "assets/branding/headers/ky_header_templates.zip",
        "assets/branding/footers/ky_footer_frameworks.zip",
        "assets/branding/footers/ky_footer_library.zip",
    ]:
        with zipfile.ZipFile(root / archive) as bundle:
            assert not any(
                "volcanic" in name or "floral_wreath" in name for name in bundle.namelist()
            )


@pytest.mark.parametrize("style", DESIGNS)
def test_uploaded_background_keeps_corner_badge_and_minimal_footer(style):
    stream = io.BytesIO()
    Image.new("RGB", (800, 400), "#123456").save(stream, format="PNG")
    header = Image.open(
        io.BytesIO(render_header(stream.getvalue(), style, height=280, framed=False))
    )
    badge = overlay(style, framed=False, height=280)
    assert header.getpixel((800, 140))[:3] == (18, 52, 86)
    assert (
        header.crop((24, 24, 214, 81)).tobytes()
        != Image.new("RGBA", (190, 57), "#123456").tobytes()
    )
    assert badge.getchannel("A").getbbox() == (24, 24, 214, 81)
    footer = Image.open(io.BytesIO(render_footer(stream.getvalue(), style, centered=True, dim=0)))
    assert footer.getpixel((206, 160))[:3] == (18, 52, 86)
    assert centered_overlay(style, footer.size).getchannel("A").getbbox()


@pytest.mark.parametrize("style", DESIGNS)
def test_flower_layout_supports_compact_and_tall_exports(style):
    for size in [(1600, 240), (1600, 800), (2176, 210)]:
        image = floral_background(style, size)
        assert image.size == size
        assert image.mode == "RGBA"
