import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from PIL import Image, ImageChops

from ky_bot.cogs.headers import Headers
from ky_bot.services.footers import MAX_UPLOAD, FooterError
from ky_bot.services.headers import DESIGNS, SIZE, STYLES, design_file, overlay, render_header
from ky_bot.services.typography import TypographyError, font_file, text_mask


def png(image):
    stream = io.BytesIO()
    image.save(stream, format="PNG")
    return stream.getvalue()


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("framed", (True, False))
def test_uploaded_background_and_corner_brand_are_preserved(style, framed):
    data = render_header(png(Image.new("RGB", (500, 500), "red")), style, framed=framed)
    image = Image.open(io.BytesIO(data))
    assert image.size == SIZE
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((800, 260)) == (255, 0, 0, 255)
    assert image.getpixel((800, 440)) == (255, 0, 0, 255)
    brand_area = (72, 68, 343, 149) if framed else (24, 24, 295, 105)
    assert overlay(style, framed=framed).crop(brand_area).getbbox() is not None
    if not framed:
        assert overlay(style, framed=False).getbbox() == brand_area
    assert "slogan" not in [parameter.name for parameter in Headers.header.parameters]


@pytest.mark.parametrize("name", DESIGNS)
@pytest.mark.parametrize("framed", (True, False))
def test_all_presets_are_available_with_and_without_frame(name, framed):
    image = Image.open(io.BytesIO(design_file(name, framed=framed)))
    assert image.size == SIZE


def test_custom_crop_and_invalid_backgrounds():
    image = Image.new("RGB", (200, 1000), "red")
    image.paste("blue", (0, 500, 200, 1000))
    upper = Image.open(io.BytesIO(render_header(png(image), crop_y=0)))
    lower = Image.open(io.BytesIO(render_header(png(image), crop_y=100)))
    assert upper.getpixel((800, 260))[:3] == (255, 0, 0)
    assert lower.getpixel((800, 260))[:3] == (0, 0, 255)
    with pytest.raises(FooterError):
        render_header(b"not an image")
    with pytest.raises(FooterError, match="Crop"):
        render_header(png(image), crop_x=101)
    with pytest.raises(FooterError, match="Choose"):
        render_header(png(image), "invalid")


@pytest.mark.parametrize("style", STYLES)
def test_large_bold_title_has_two_rules_and_no_clipping(style):
    try:
        path = font_file("display")
    except TypographyError:
        pytest.skip("Licensed fonts remain private production assets.")
    assert path.name.startswith("Vonca-Bold.")
    base = png(Image.new("RGB", SIZE, "#546070"))
    titled = Image.open(io.BytesIO(render_header(base, style, title="COMMAND CENTER")))
    untitled = Image.open(io.BytesIO(render_header(base, style)))
    mask = text_mask("COMMAND CENTER", role="display", size=108, min_size=48, max_width=1280)
    assert mask.height > 50
    assert 252 + mask.height + 28 < SIZE[1] - 24
    left = (SIZE[0] - mask.width) // 2
    top, bottom = 224, 252 + mask.height + 28
    rule_x = left + 40
    assert titled.getpixel((rule_x, top)) == titled.getpixel((rule_x, bottom))
    assert titled.getpixel((rule_x, top)) != untitled.getpixel((rule_x, top))
    assert ImageChops.difference(titled.convert("RGB"), untitled.convert("RGB")).getbbox()


def test_blank_header_works_without_fonts_but_custom_title_explains_missing_font(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("KY_BOT_FONT_DIR", str(tmp_path))
    data = png(Image.new("RGB", SIZE, "blue"))
    assert render_header(data)
    with pytest.raises(FooterError, match="VONCA Bold"):
        render_header(data, title="SETTINGS")


async def test_header_command_returns_private_export():
    cog = Headers(SimpleNamespace())
    data = png(Image.new("RGB", (300, 300), "blue"))
    background = SimpleNamespace(size=len(data), read=AsyncMock(return_value=data))
    request = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )
    await cog.header.callback(cog, request, background, SimpleNamespace(value="gold"), framed=False)
    request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    sent = request.followup.send.call_args.kwargs
    assert sent["ephemeral"]
    assert sent["file"].filename == "ky_header_gold.png"
    sent["file"].close()


async def test_oversized_upload_is_rejected_before_download():
    cog = Headers(SimpleNamespace())
    background = SimpleNamespace(size=MAX_UPLOAD + 1, read=AsyncMock())
    request = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
    await cog.header.callback(cog, request, background, SimpleNamespace(value="gold"))
    background.read.assert_not_awaited()
    assert request.response.send_message.call_args.kwargs["ephemeral"]
