import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from ky_bot.cogs.footers import Footers
from ky_bot.services.footers import (
    MAX_UPLOAD,
    SIZE,
    STYLES,
    FooterError,
    overlay,
    read_upload,
    render_footer,
    slogan_mask,
)


def png(image):
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@pytest.mark.parametrize("style", STYLES)
def test_all_styles_allow_background_to_show_and_keep_transparency(style):
    red = Image.open(
        io.BytesIO(render_footer(png(Image.new("RGB", (500, 500), "red")), style, dim=0))
    )
    blue = Image.open(
        io.BytesIO(render_footer(png(Image.new("RGB", (500, 500), "blue")), style, dim=0))
    )
    assert red.size == SIZE and red.mode == "RGBA"
    assert red.getpixel((0, 0))[3] == 0
    assert red.getpixel((1088, 100)) == (255, 0, 0, 255)
    assert blue.getpixel((1088, 100)) == (0, 0, 255, 255)
    assert overlay(style).getpixel((1088, 100))[3] == 0


def test_crop_focal_point_changes_visible_background():
    image = Image.new("RGB", (200, 400), "red")
    image.paste("blue", (0, 200, 200, 400))
    upper = Image.open(io.BytesIO(render_footer(png(image), "gold", crop_y=0, dim=0)))
    lower = Image.open(io.BytesIO(render_footer(png(image), "gold", crop_y=100, dim=0)))
    assert upper.getpixel((1088, 100))[:3] == (255, 0, 0)
    assert lower.getpixel((1088, 100))[:3] == (0, 0, 255)


def test_footer_fills_rectangular_corner_area():
    image = Image.open(
        io.BytesIO(render_footer(png(Image.new("RGB", SIZE, "red")), "dark_silver", dim=0))
    )
    assert image.getpixel((20, 20)) == (255, 0, 0, 255)
    assert image.getpixel((SIZE[0] - 21, SIZE[1] - 21)) == (255, 0, 0, 255)


@pytest.mark.parametrize("style", STYLES)
def test_thin_outline_matches_every_edge_and_canvas_size(style):
    from ky_bot.services.footers import rectangular_frame

    short = rectangular_frame(style, (1600, 240))
    tall = rectangular_frame(style, (2176, 800))
    top = short.crop((20, 0, 120, 12))
    side = short.crop((0, 20, 12, 120)).transpose(Image.Transpose.ROTATE_270)
    assert top.tobytes() == side.tobytes()
    assert top.tobytes() == tall.crop((20, 0, 120, 12)).tobytes()


@pytest.mark.parametrize(
    "data", [b"not an image", b"", b"x" * (MAX_UPLOAD + 1)], ids=["invalid", "empty", "oversize"]
)
def test_invalid_uploads_are_actionable(data):
    with pytest.raises(FooterError):
        read_upload(data)


def test_dimension_and_animation_limits():
    with pytest.raises(FooterError, match="8192"):
        read_upload(png(Image.new("RGB", (8193, 1))))
    out = io.BytesIO()
    Image.new("RGB", (10, 10)).save(
        out, format="PNG", save_all=True, append_images=[Image.new("RGB", (10, 10), "red")]
    )
    with pytest.raises(FooterError, match="still image"):
        read_upload(out.getvalue())


def test_custom_icons_are_cropped_and_recolored_independently():
    image = Image.new("RGBA", (100, 100))
    image.paste("red", (25, 25, 75, 75))
    data = png(image)
    assert read_upload(data, icon=True).size == (50, 50)
    default = overlay("gold")
    custom = overlay("gold", left_icon=data)
    assert default.crop((96, 85, 316, 235)).tobytes() != custom.crop((96, 85, 316, 235)).tobytes()
    assert (
        default.crop((1860, 85, 2080, 235)).tobytes()
        == custom.crop((1860, 85, 2080, 235)).tobytes()
    )
    with pytest.raises(FooterError, match="transparent background"):
        read_upload(png(Image.new("RGB", (50, 50))), icon=True)


def test_custom_slogan_requires_font_and_rejects_multiline(tmp_path, monkeypatch):
    monkeypatch.setenv("KY_BOT_FONT_DIR", str(tmp_path))
    with pytest.raises(FooterError, match="VONCA"):
        slogan_mask("MY SERVER", None)
    with pytest.raises(FooterError, match="single-line"):
        slogan_mask("MY\nSERVER", None)
    font = Path("C:/Windows/Fonts/arial.ttf")
    if font.exists():
        assert slogan_mask("TEST BRAND", font).getbbox() is not None


async def test_command_defers_and_sends_private_export():
    cog = Footers(SimpleNamespace(settings=SimpleNamespace(footer_font_path=None)))
    data = png(Image.new("RGB", (100, 100), "blue"))
    attachment = SimpleNamespace(size=len(data), read=AsyncMock(return_value=data))
    request = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await cog.footer.callback(cog, request, attachment, SimpleNamespace(value="gold"))
    request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    sent = request.followup.send.call_args.kwargs
    assert sent["ephemeral"] is True and sent["file"].filename == "ky_footer_gold.png"
    sent["file"].close()


async def test_command_rejects_large_upload_before_reading():
    cog = Footers(SimpleNamespace(settings=SimpleNamespace(footer_font_path=None)))
    attachment = SimpleNamespace(size=MAX_UPLOAD + 1, read=AsyncMock())
    request = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()))
    await cog.footer.callback(cog, request, attachment, SimpleNamespace(value="gold"))
    attachment.read.assert_not_awaited()
    assert request.response.send_message.call_args.kwargs["ephemeral"] is True
