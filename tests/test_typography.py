import pytest

from ky_bot.design.typography import TAGLINE, TypographyError, font_file, text_mask


def test_tagline_uses_real_light_font_and_fits():
    try:
        path = font_file("tagline")
    except TypographyError:
        pytest.skip("Licensed local fonts are not distributed with the test suite.")
    assert path.stem == "Vonca-Light"
    mask = text_mask(TAGLINE)
    assert mask.getbbox() is not None
    assert mask.width <= 1002
    assert mask.getbbox()[2] < mask.width
    assert mask.getbbox()[3] < mask.height


def test_text_that_cannot_fit_is_rejected():
    try:
        font_file("tagline")
    except TypographyError:
        pytest.skip("Licensed local fonts are not distributed with the test suite.")
    with pytest.raises(TypographyError, match="too wide"):
        text_mask("W" * 80, max_width=10)


def test_missing_private_font_has_clear_error(tmp_path, monkeypatch):
    monkeypatch.setenv("KY_BOT_FONT_DIR", str(tmp_path))
    with pytest.raises(TypographyError, match="VONCA Light"):
        text_mask(TAGLINE)


def test_ornament_rotates_without_rotating_title(monkeypatch):
    from PIL import Image

    import ky_bot.design.typography as typography

    lettering = Image.new("L", (3, 4), 80)
    lettering.putpixel((0, 0), 255)
    monkeypatch.setattr(typography, "text_mask", lambda *args, **kwargs: lettering)
    divider = Image.new("L", (4, 4))
    divider.putpixel((0, 0), 255)
    result = typography.divided_title_mask(
        "LEFT 𖥸 RIGHT", divider, size=10, min_size=10, max_width=100
    )
    gap = round(10 * 0.35)
    assert result.crop((0, 0, 3, 4)).tobytes() == lettering.tobytes()
    ornament = result.crop((3 + gap, 0, 7 + gap, 4))
    assert ornament.tobytes() == divider.transpose(Image.Transpose.ROTATE_180).tobytes()
