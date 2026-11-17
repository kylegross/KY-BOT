import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from ky_bot.advice.command import Advice
from ky_bot.advice.menus import Admin
from ky_bot.advice.store import Store


class MultipleAdviceChannelsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = await Store.open(":memory:")
        self.revision, _ = await self.store.configure(
            1,
            10,
            None,
            enabled=True,
            cooldown=0,
            access_channel=20,
            access_role=30,
            advice_role=40,
        )

    async def asyncTearDown(self):
        await self.store.close()

    async def second(self, **changes):
        return await self.store.configure(
            1,
            11,
            None,
            **(
                dict(enabled=True, cooldown=0, access_channel=20, access_role=31, advice_role=41)
                | changes
            ),
        )

    async def test_settings_questions_and_roles_are_independent(self):
        revision, _ = await self.second(color="FFAA00", response_approval=True, queue_channel=50)
        first = await self.store.submit(1, 10, 100, [30], "First question", 1, self.revision)
        second = await self.store.submit(1, 11, 100, [31], "Second question", 2, revision)
        self.assertEqual(first["data"]["number"], 1)
        self.assertEqual(second["data"]["number"], 1)
        with self.assertRaisesRegex(ValueError, "Join"):
            await self.store.submit(1, 11, 100, [30], "Wrong access role", 3, revision)
        self.assertNotEqual(
            (await self.store.config(1, 10))[1]["color"],
            (await self.store.config(1, 11))[1]["color"],
        )
        self.assertFalse((await self.store.config(1, 10))[1]["response_approval"])

    async def test_shared_entrance_panels_do_not_replace_one_another(self):
        await self.second()
        await self.store.access_panel(1, 10, channel=20, message=100)
        await self.store.access_panel(1, 11, channel=20, message=200)
        first = await self.store.access_panel(1, 10, channel=20, message=101)
        self.assertEqual(first["retired"], [[20, 100]])
        other = await self.store.access_panel(1, 11)
        self.assertEqual(other["message"], 200)
        self.assertEqual(other["retired"], [])
        await self.store.access_retired_done(1, 10, 20, 100)
        self.assertEqual((await self.store.access_panel(1, 10))["retired"], [])

    async def test_existing_single_channel_panel_is_adopted(self):
        await self.store.panel(1, 20, message=777)
        self.assertEqual((await self.store.access_panel(1, 10))["message"], 777)

    async def test_access_panel_never_adopts_another_ask_channels_submission_panel(self):
        await self.second(access_channel=10)
        await self.store.panel(1, 10, message=777)
        self.assertEqual((await self.store.access_panel(1, 11))["message"], 0)
        saved = await self.store.access_panel(1, 11, channel=10, message=888)
        self.assertEqual(saved["retired"], [])
        self.assertEqual((await self.store.panel(1, 10))[0], 777)

    async def test_publish_replaces_only_the_selected_setups_panel(self):
        await self.second()
        cog = Advice(SimpleNamespace())
        cog.store = self.store
        cog.apply_access = AsyncMock()
        cog.send_panel = AsyncMock(
            side_effect=[SimpleNamespace(id=100), SimpleNamespace(id=200), SimpleNamespace(id=101)]
        )
        delete = AsyncMock()
        target = SimpleNamespace(
            id=20, get_partial_message=Mock(return_value=SimpleNamespace(delete=delete))
        )
        with (
            patch("ky_bot.advice.command.destination", return_value=target),
            patch("ky_bot.advice.command.access_panel", return_value=Mock()),
        ):
            for channel in (10, 11, 10):
                await cog.publish_panel(SimpleNamespace(id=1), channel, access=True)
        target.get_partial_message.assert_called_once_with(100)
        delete.assert_awaited_once()
        self.assertEqual((await self.store.access_panel(1, 11))["message"], 200)

    async def test_saved_channel_picker_and_pagination_stay_within_discord_limits(self):
        for channel in range(100, 127):
            await self.store.configure(1, channel, None)
        menu = Admin(SimpleNamespace(store=self.store), 7, 1)
        menu.allowed = AsyncMock()
        i = SimpleNamespace(guild_id=1)
        with patch("ky_bot.advice.menus.display_menu", new=AsyncMock()):
            await menu.show(i, "channels")
            picker = next(
                item for item in menu.walk_children() if isinstance(item, discord.ui.Select)
            )
            self.assertEqual(len(picker.options), 25)
            self.assertLessEqual(menu.total_children_count, 40)
            menu.channel_page = 1
            await menu.show(i, "channels")
            picker = next(
                item for item in menu.walk_children() if isinstance(item, discord.ui.Select)
            )
            self.assertEqual(len(picker.options), 3)
        menu.stop()

    async def test_join_buttons_use_only_their_own_setup_and_reject_crossed_panels(self):
        await self.second()
        await self.store.access_panel(1, 10, channel=20, message=100)
        await self.store.access_panel(1, 11, channel=20, message=200)
        member = SimpleNamespace(roles=[], add_roles=AsyncMock())
        guild = SimpleNamespace(fetch_member=AsyncMock(return_value=member))
        i = SimpleNamespace(
            guild_id=1,
            guild=guild,
            channel_id=20,
            user=SimpleNamespace(id=123),
            message=SimpleNamespace(id=100),
        )
        cog = Advice(SimpleNamespace())
        cog.store = self.store

        def roles(guild, config):
            return [SimpleNamespace(id=config[key]) for key in ("access_role", "advice_role")]

        with (
            patch("ky_bot.advice.command.roles_for", side_effect=roles),
            patch("ky_bot.advice.command.send_private", new=AsyncMock()),
        ):
            await cog.role_action(i, 10, "join")
            i.message.id = 200
            await cog.role_action(i, 11, "join")
            with self.assertRaisesRegex(ValueError, "replaced"):
                await cog.role_action(i, 10, "join")
        self.assertEqual([call.args[0].id for call in member.add_roles.await_args_list], [30, 31])

    async def test_panel_ownership_persists_after_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "advice.sqlite3"
            store = await Store.open(path)
            await store.configure(1, 10, None, access_channel=20)
            await store.configure(1, 11, None, access_channel=20)
            await store.access_panel(1, 10, channel=20, message=100)
            await store.access_panel(1, 11, channel=20, message=200)
            await store.close()
            store = await Store.open(path)
            try:
                self.assertEqual((await store.access_panel(1, 10))["message"], 100)
                self.assertEqual((await store.access_panel(1, 11))["message"], 200)
            finally:
                await store.close()
