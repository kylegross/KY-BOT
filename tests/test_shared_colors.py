import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from ky_bot.admin_functions.settings_repository import open_settings
from ky_bot.design.saved_colors import ColorLibrary, open_library


def interaction(repository, guild=1):
    return SimpleNamespace(
        user=SimpleNamespace(id=7),
        guild_id=guild,
        client=SimpleNamespace(server_settings=SimpleNamespace(repository=repository)),
        permissions=discord.Permissions(administrator=True),
        response=SimpleNamespace(
            is_done=lambda: False, send_message=AsyncMock(), defer=AsyncMock()
        ),
        edit_original_response=AsyncMock(),
    )


class SharedColorTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_colors_are_reused_across_menus_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.sqlite3"
            async with open_settings(path) as repository:
                i = interaction(repository)
                advice_commit, embed_commit = AsyncMock(), AsyncMock()
                advice = await open_library(i, {}, {"text_color": "Heading text"}, advice_commit)
                await advice.save_color(i, None, "Emerald", "18AA77")
                await advice.save_color(i, None, "Rose", "#EE7799")
                embed = await open_library(i, {}, {"line_color": "Lines"}, embed_commit)
                self.assertEqual(embed.colors, {"Emerald": "#18AA77", "Rose": "#EE7799"})
                embed.selected = "Emerald"
                await embed.apply(i)
                self.assertEqual(embed_commit.await_args.args[2:], ("line_color", "#18AA77"))
                advice_commit.assert_not_awaited()
                self.assertEqual((await repository.get_colors(2))[1], {})
                advice.stop()
                embed.stop()
            async with open_settings(path) as repository:
                self.assertEqual(len((await repository.get_colors(1))[1]), 2)

    async def test_legacy_import_does_not_resurrect_an_edited_color(self):
        async with open_settings(":memory:") as repository:
            i = interaction(repository)
            local = {"Emerald": "#18AA77"}
            view = await open_library(i, local, {"text_color": "Text"}, AsyncMock())
            await view.save_color(i, "Emerald", "Emerald", "#116644")
            again = await open_library(i, local, {"text_color": "Text"}, AsyncMock())
            self.assertEqual(again.colors, {"Emerald": "#116644"})
            conflicting = await open_library(
                i, {"Emerald": "#334455"}, {"text_color": "Text"}, AsyncMock()
            )
            self.assertEqual(conflicting.colors["Emerald"], "#116644")
            self.assertEqual(conflicting.colors["Emerald (2)"], "#334455")
            for item in (view, again, conflicting):
                item.stop()

    async def test_concurrent_editors_do_not_overwrite_saved_colors(self):
        async with open_settings(":memory:") as repository:
            i = interaction(repository)
            first = await open_library(i, {}, {"text_color": "Text"}, AsyncMock())
            second = await open_library(i, {}, {"text_color": "Text"}, AsyncMock())
            await first.save_color(i, None, "Emerald", "#18AA77")
            with self.assertRaisesRegex(ValueError, "Saved colors changed"):
                await second.save_color(i, None, "Rose", "#EE7799")
            self.assertEqual((await repository.get_colors(1))[1], {"Emerald": "#18AA77"})
            first.stop()
            second.stop()

    async def test_large_shared_library_paginates_within_discord_limits(self):
        view = ColorLibrary(
            7,
            1,
            {f"Color {n}": "#123456" for n in range(60)},
            {"line_color": "Lines"},
            AsyncMock(),
            shared=True,
        )
        selects = [item for item in view.children if isinstance(item, discord.ui.Select)]
        self.assertEqual(len(selects[0].options), 25)
        i = interaction(None)
        await view.next_page(i)
        self.assertEqual(view.page, 1)
        self.assertTrue(
            all(
                len(item.options) <= 25
                for item in view.children
                if isinstance(item, discord.ui.Select)
            )
        )
        view.stop()
