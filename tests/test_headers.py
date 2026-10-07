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
    brand_area = (72, 68, 262, 125) if framed else (24, 24, 214, 81)
    assert overlay(style, framed=framed).crop(brand_area).getbbox() is not None
    if not framed:
        assert overlay(style, framed=False).getbbox() == brand_area
    assert "slogan" not in [parameter.name for parameter in Headers.header.parameters]


@pytest.mark.parametrize("name", DESIGNS)
@pytest.mark.parametrize("framed", (True, False))
@pytest.mark.parametrize("compact", (True, False))
def test_all_presets_are_available_with_and_without_frame(name, framed, compact):
    image = Image.open(io.BytesIO(design_file(name, framed=framed, compact=compact)))
    assert image.size == (1600, 280 if compact else 520)


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
    y = (SIZE[1] - mask.height) // 2
    assert y + mask.height + 28 < SIZE[1] - 24
    left = (SIZE[0] - mask.width) // 2
    top, bottom = y - 28, y + mask.height + 28
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
    await cog.header.callback(
        cog, request, background, SimpleNamespace(value="gold"), framed=False, height=280
    )
    request.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
    sent = request.followup.send.call_args.kwargs
    assert sent["ephemeral"]
    assert sent["file"].filename == "ky_header_gold.png"
    assert Image.open(sent["file"].fp).size == (1600, 280)
    sent["file"].close()


async def test_oversized_upload_is_rejected_before_download():
    cog = Headers(SimpleNamespace())
    background = SimpleNamespace(size=MAX_UPLOAD + 1, read=AsyncMock())
    request = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))
    await cog.header.callback(cog, request, background, SimpleNamespace(value="gold"))
    background.read.assert_not_awaited()
    assert request.response.send_message.call_args.kwargs["ephemeral"]


@pytest.mark.parametrize("height", [240, 280, 520, 800])
@pytest.mark.parametrize("framed", [True, False])
def test_custom_height_preserves_brand_and_transparent_corners(height, framed):
    data = png(Image.new("RGB", (300, 300), "blue"))
    image = Image.open(io.BytesIO(render_header(data, height=height, framed=framed)))
    assert image.size == (1600, height)
    assert image.getpixel((0, 0))[3] == 0
    assert image.getpixel((1599, height - 1))[3] == 0
    brand_area = (72, 68, 262, 125) if framed else (24, 24, 214, 81)
    original = overlay("gold", framed=framed).crop(brand_area)
    adjusted = overlay("gold", framed=framed, height=height).crop(brand_area)
    assert original.tobytes() == adjusted.tobytes()


@pytest.mark.parametrize("height", [239, 801, 280.5, True])
def test_invalid_height_rejected(height):
    with pytest.raises(FooterError, match="height"):
        render_header(b"", height=height)


@pytest.mark.parametrize("framed", [True, False])
def test_header_fills_rectangular_corner_area(framed):
    image = Image.open(io.BytesIO(render_header(
        png(Image.new("RGB", SIZE, "red")), framed=framed, height=280
    )))
    assert image.getpixel((20, 20)) == (255, 0, 0, 255)
    assert image.getpixel((1580, 260)) == (255, 0, 0, 255)


@pytest.mark.parametrize("height", [240, 280, 800])
@pytest.mark.parametrize("framed", [True, False])
def test_title_fits_custom_height(height, framed):
    try:
        font_file("display")
    except TypographyError:
        pytest.skip("Licensed fonts remain private production assets.")
    data = png(Image.new("RGB", SIZE, "blue"))
    result = Image.open(io.BytesIO(render_header(
        data, title="COMMAND CENTER", height=height, framed=framed
    )))
    plain = Image.open(io.BytesIO(render_header(data, height=height, framed=framed)))
    area = ImageChops.difference(result.convert("RGB"), plain.convert("RGB")).getbbox()
    assert area is not None
    # Lettering and both rules must fit within the frame's bottom safe margin.
    mask = text_mask(
        "COMMAND CENTER", role="display", size=min(108, round(height * 108 / 520)),
        min_size=40, max_width=1280,
    )
    y = (height - mask.height) // 2
    assert y + mask.height + min(28, round(height * 28 / 520)) < height - 24
