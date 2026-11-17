import asyncio
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from ky_bot.confessions.menus import Admin as ConfessionAdmin
from ky_bot.confessions.store import Store as ConfessionStore
from ky_bot.custom_embeds.store import Store as EmbedStore
from ky_bot.custom_embeds.views import Session
from ky_bot.design.editor import DesignSession
from ky_bot.design.saved_colors import ColorLibrary, ColorModal, validate_colors
from ky_bot.tickets.store import Store as TicketStore
from ky_bot.tickets.store import defaults, new_panel
from ky_bot.tickets.views import Admin as TicketAdmin


def interaction():
    return SimpleNamespace(
        user=SimpleNamespace(id=7),
        guild_id=1,
        permissions=discord.Permissions(administrator=True),
        response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: False),
        edit_original_response=AsyncMock(),
        client=SimpleNamespace(),
        guild=SimpleNamespace(),
    )


class SavedColorTests(unittest.IsolatedAsyncioTestCase):
    async def test_adding_editing_and_applying_preserves_applied_copies(self):
        commits = []

        async def commit(i, colors, target, color):
            commits.append((dict(colors), target, color))

        view = ColorLibrary(7, 1, {}, {"text_color": "Heading text", "line_color": "Lines"}, commit)
        i = interaction()
        await view.save_color(i, None, "Emerald", "18aa77")
        await view.save_color(i, None, "Rose", "#ee7799")
        view.selected = "Emerald"
        await view.apply(i)
        applied = commits[-1][2]
        await view.save_color(i, "Emerald", "Emerald", "#116644")
        self.assertEqual(applied, "#18AA77")
        self.assertIsNone(commits[-1][1])
        self.assertEqual(view.colors, {"Emerald": "#116644", "Rose": "#EE7799"})
        with self.assertRaisesRegex(ValueError, "already exists"):
            await view.save_color(i, None, "Emerald", "#000000")
        view.stop()

    async def test_custom_color_is_previewed_before_it_is_saved(self):
        commit = AsyncMock()
        library = ColorLibrary(7, 1, {}, {"text_color": "Heading text"}, commit)
        modal = ColorModal(library)
        modal.name_field._value = "Emerald"
        modal.hex_field._value = "#18aa77"
        event = interaction()
        event.followup = SimpleNamespace(send=AsyncMock())
        await modal.on_submit(event)
        commit.assert_not_awaited()
        self.assertEqual(library.colors, {})
        review = event.followup.send.await_args.kwargs["view"]
        self.assertEqual(review.palette["base"], "#18AA77")
        await review.use(event)
        self.assertEqual(library.colors, {"Emerald": "#18AA77"})
        commit.assert_awaited_once()
        self.assertIn("Preview", [getattr(child, "label", None) for child in library.children])
        library.stop()

    async def test_named_colors_persist_in_confessions_and_custom_embeds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bot.sqlite3"
            store = await ConfessionStore.open(path)
            rev, config = await store.configure(1, 10, None)
            menu = SimpleNamespace(
                revision=rev, channel=10, guild=1, config=config, cog=SimpleNamespace(store=store)
            )
            with patch("ky_bot.design.saved_colors.open_library", new=AsyncMock()) as launch:
                await ConfessionAdmin.open_saved_colors(menu, interaction())
            commit = launch.await_args.args[3]
            colors = {"Emerald": "#18AA77", "Rose": "#EE7799"}
            await commit(interaction(), colors, "text_color", "#18AA77")
            await commit(interaction(), colors, "line_color", "#EE7799")
            _, unchanged = await store.config(1, 11)
            self.assertEqual(unchanged["saved_colors"], {})
            await store.close()
            store = await ConfessionStore.open(path)
            _, saved = await store.config(1, 10)
            self.assertEqual(saved["saved_colors"], colors)
            self.assertEqual(saved["text_color"], "#18AA77")
            self.assertEqual(saved["line_color"], "#EE7799")
            await store.close()

            embeds = await EmbedStore.open(path)
            ident = await embeds.create(1)
            other = await embeds.create(1)
            session = SimpleNamespace(
                row=await embeds.get(1, ident),
                guild=1,
                ident=ident,
                lock=asyncio.Lock(),
                cog=SimpleNamespace(store=embeds, lock=lambda _: asyncio.Lock()),
            )
            with patch("ky_bot.design.saved_colors.open_library", new=AsyncMock()) as launch:
                await Session.open_saved_colors(session, interaction())
            commit = launch.await_args.args[3]
            await commit(interaction(), colors, "header_text_color", "#18AA77")
            await commit(interaction(), colors, "line_color", "#EE7799")
            self.assertEqual((await embeds.get(1, other))["draft"]["saved_colors"], {})
            await embeds.close()
            embeds = await EmbedStore.open(path)
            saved = (await embeds.get(1, ident))["draft"]
            self.assertEqual(saved["saved_colors"], colors)
            self.assertEqual(saved["header_text_color"], "#18AA77")
            await embeds.close()

    async def test_ticket_panel_library_preserves_other_panels(self):
        store = await TicketStore.open(":memory:")
        try:
            config = defaults()
            key, other = "a" * 32, "b" * 32
            config["panels"] = {key: new_panel("First"), other: new_panel("Other")}
            revision = await store.configure(1, None, config)
            menu = SimpleNamespace(
                revision=revision,
                config=config,
                selected=key,
                offset=0,
                opening=False,
                parent=SimpleNamespace(guild_id=1),
                cog=SimpleNamespace(store=store),
            )
            with patch("ky_bot.design.saved_colors.open_library", new=AsyncMock()) as launch:
                await TicketAdmin.open_saved_colors(menu, interaction(), position="before")
            commit = launch.await_args.args[3]
            colors = {"Emerald": "#18AA77", "Rose": "#EE7799"}
            await commit(interaction(), colors, "section_color", "#18AA77")
            await commit(interaction(), colors, "section_line", "#EE7799")
            self.assertEqual(menu.config["panels"][key]["saved_colors"], colors)
            self.assertEqual(menu.config["panels"][key]["before_color"], "18AA77")
            self.assertEqual(menu.config["panels"][other]["saved_colors"], {})
        finally:
            await store.close()

    async def test_dmfr_and_checklist_libraries_stay_in_the_selected_draft(self):
        i = interaction()
        session = DesignSession(i, None, None, {})
        for target in ("askdm", "checklist"):
            await session.select([target])
            before = copy.deepcopy(session.profiles)
            with patch("ky_bot.design.saved_colors.open_library", new=AsyncMock()) as launch:
                await session.open_saved_colors(i)
            commit = launch.await_args.args[3]
            allowed = launch.await_args.kwargs["allowed"]
            shared_parent = object()
            session.command_center = shared_parent
            self.assertTrue(await allowed(i))
            self.assertIs(session.command_center, shared_parent)
            await commit(i, {"Emerald": "#18AA77", "Rose": "#EE7799"}, "line_color", "#18AA77")
            self.assertEqual(session.drafts[target]["line_color"], "#18AA77")
            self.assertEqual(session.profiles, before)
            self.assertEqual(len(session.drafts[target]["saved_colors"]), 2)
        self.assertEqual(session.drafts["askdm"]["line_color"], "#18AA77")
        session.generation += 1
        with patch("ky_bot.design.editor.send_private", new=AsyncMock()):
            self.assertFalse(await allowed(i))

    async def test_limits_and_permissions(self):
        with self.assertRaises(ValueError):
            validate_colors({str(n): "#123456" for n in range(26)})
        view = ColorLibrary(7, 1, {}, {"line_color": "Lines"}, AsyncMock())
        i = interaction()
        i.user.id = 8
        with patch("ky_bot.design.saved_colors.send_private", new=AsyncMock()):
            self.assertFalse(await view.interaction_check(i))
        view.stop()
