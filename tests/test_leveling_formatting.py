import io
import unittest
from types import SimpleNamespace

from PIL import Image

from ky_bot.leveling.artwork import announcement, level_card
from ky_bot.leveling.menu import CardFormatting
from ky_bot.leveling.store import LevelStore, defaults, validate


class LevelFormattingTests(unittest.IsolatedAsyncioTestCase):
    async def test_settings_and_rendering(self):
        store = await LevelStore.open(":memory:")
        revision, config = await store.config(123)
        _, saved = await store.configure(
            123,
            revision,
            card_title="CONGRATS",
            card_height=300,
            name_size=110,
            announcement_text="{mention} reached level {level}!",
        )
        assert (await store.config(123))[1] == saved
        try:
            await store.configure(123, revision, card_height=420)
        except ValueError:
            pass
        else:
            raise AssertionError("stale edit accepted")
        member = SimpleNamespace(mention="<@1>", display_name="Delulu")
        assert announcement(saved, member, 3) == "<@1> reached level 3!"
        for changes in (
            {"card_height": 299},
            {"name_size": 111},
            {"announcement_text": "{name.__class__}"},
            {"announcement_text": "{level:1000000}"},
            {"announcement_text": "{oops}"},
            {"card_title": ""},
        ):
            try:
                validate(defaults() | changes)
            except ValueError:
                pass
            else:
                raise AssertionError(changes)
        session = SimpleNamespace(generation=1, revision="a", config=defaults())
        modal = CardFormatting(session)
        assert len(modal.children) == 5
        for height in (300, 340, 420):
            for name in ("Delulu", "A very long member name " * 5):
                data = level_card(
                    name,
                    10000,
                    "Nov 05, 2026",
                    settings=defaults()
                    | {"card_height": height, "name_size": 110, "title_size": 80},
                )
                assert Image.open(io.BytesIO(data)).size == (1440, height)
        await store.close()
