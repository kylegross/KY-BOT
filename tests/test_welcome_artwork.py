import io
from pathlib import Path

import pytest
from PIL import Image

from ky_bot.database.settings import WelcomeArtwork, open_settings
from ky_bot.services.settings import SettingsService
from ky_bot.services.welcome import normalize_background, render_welcome


def background():
    stream = io.BytesIO()
    Image.new("RGB", (400, 200), "#102030").save(stream, format="PNG")
    return stream.getvalue()


async def test_artwork_persists_per_server_and_reset_preserves_style(tmp_path):
    path = tmp_path / "settings.db"
    async with open_settings(path) as repo:
        service = SettingsService(repo)
        await service.set_welcome_background(1, background())
        await service.set_welcome_appearance(1, "Hello", "#abcdef", "20")
    async with open_settings(path) as repo:
        artwork = await repo.get_welcome_artwork(1)
        assert artwork.title == "Hello"
        assert artwork.accent == "ABCDEF"
        assert artwork.dim == 20
        assert artwork.background
        assert (await repo.get(1)).welcome_custom_background
        assert await repo.get_welcome_artwork(2) == WelcomeArtwork()
        await repo.set_welcome_background(1, None)
        assert (await repo.get_welcome_artwork(1)).title == "Hello"
        assert not (await repo.get(1)).welcome_custom_background


@pytest.mark.parametrize(
    "title,accent,dim",
    [
        ("", "ABCDEF", "20"),
        ("Hello", "xyz", "20"),
        ("Hello", "ABCDEF", "81"),
        ("Hello", "ABCDEF", "no"),
    ],
)
async def test_invalid_style_is_not_saved(title, accent, dim):
    async with open_settings(Path(":memory:")) as repo:
        with pytest.raises(ValueError):
            await SettingsService(repo).set_welcome_appearance(1, title, accent, dim)
        assert await repo.get_welcome_artwork(1) == WelcomeArtwork()


def test_background_and_custom_card_are_valid_images():
    normalized = normalize_background(background())
    assert Image.open(io.BytesIO(normalized)).size == (1200, 800)
    card = render_welcome(
        WelcomeArtwork(background=normalized),
        member_name="Kyle",
        server_name="Community",
        message="Welcome!",
        avatar=b"invalid",
    )
    assert Image.open(io.BytesIO(card)).size == (1200, 800)
    with pytest.raises(ValueError):
        normalize_background(b"not an image")
