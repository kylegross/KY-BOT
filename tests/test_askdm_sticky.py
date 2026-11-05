import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from ky_bot.askdm.command import AskDM
from ky_bot.askdm.store import Store


class StickyMenuTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "settings.sqlite3"
        self.store = await Store.open(self.path)
        self.revision, _ = await self.store.configure(
            1,
            None,
            enabled=True,
            public=True,
            channel=10,
            log_channel=20,
            panel=100,
            panel_channel=10,
        )
        self.messages = [100]
        self.deletes = {}
        self.next_id = 200

        async def history(limit=1):
            if self.messages:
                yield SimpleNamespace(id=self.messages[-1])

        def partial(mid):
            if mid not in self.deletes:

                async def remove():
                    if mid in self.messages:
                        self.messages.remove(mid)

                self.deletes[mid] = AsyncMock(side_effect=remove)
            return SimpleNamespace(delete=self.deletes[mid])

        self.channel = SimpleNamespace(
            id=10, history=Mock(side_effect=history), get_partial_message=Mock(side_effect=partial)
        )

        async def send(channel, view):
            self.next_id += 1
            mid = self.next_id
            self.messages.append(mid)
            return SimpleNamespace(id=mid, delete=partial(mid).delete)

        self.send = AsyncMock(side_effect=send)
        self.make_cog()

    def make_cog(self):
        self.cog = AskDM(SimpleNamespace(user=SimpleNamespace(id=999)))
        self.cog.store = self.store
        self.cog.channel = AsyncMock(return_value=self.channel)
        self.cog.send_panel = self.send

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    async def test_new_request_moves_menu_and_removes_only_old_copy(self):
        self.messages.append(1000)
        await self.cog.bump_sticky(1)
        _, config = await self.store.config(1)
        self.assertEqual(config["panel"], 201)
        self.assertEqual(self.messages, [1000, 201])
        self.deletes[100].assert_awaited_once()
        self.assertEqual(config["retired_panels"], [])

    async def test_already_latest_and_simultaneous_bumps_do_not_repost(self):
        await self.cog.bump_sticky(1)
        self.send.assert_not_awaited()
        self.messages.append(1000)
        await asyncio.gather(*(self.cog.bump_sticky(1) for _ in range(4)))
        self.send.assert_awaited_once()
        self.assertEqual(self.messages, [1000, 201])

    async def test_hidden_or_disabled_requests_do_not_move_menu(self):
        self.messages.append(1000)
        revision, _ = await self.store.config(1)
        revision, _ = await self.store.configure(1, revision, public=False)
        await self.cog.bump_sticky(1)
        revision, _ = await self.store.configure(1, revision, public=True, enabled=False)
        await self.cog.bump_sticky(1)
        self.send.assert_not_awaited()
        self.assertIn(100, self.messages)

    async def test_operational_move_keeps_admin_settings_revision_valid(self):
        self.messages.append(1000)
        await self.cog.bump_sticky(1)
        revision, config = await self.store.config(1)
        self.assertEqual(revision, self.revision)
        await self.store.configure(1, self.revision, sender_roles=[700])
        _, config = await self.store.config(1)
        self.assertEqual(config["panel"], 201)
        self.assertEqual(config["sender_roles"], [700])

    async def test_failed_old_deletion_recovers_after_restart_without_extra_copy(self):
        self.messages.append(1000)
        self.channel.get_partial_message(100)
        self.deletes[100].side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "No access"
        )
        await self.cog.bump_sticky(1)
        _, config = await self.store.config(1)
        self.assertEqual(config["panel"], 201)
        self.assertEqual(config["retired_panels"], [dict(channel=10, message=100)])
        await self.cog.bump_sticky(1)
        self.send.assert_awaited_once()
        await self.store.close()
        self.store = await Store.open(self.path)
        self.make_cog()

        async def remove():
            self.messages.remove(100)

        self.deletes[100].side_effect = remove
        await self.cog.bump_sticky(1)
        self.send.assert_awaited_once()
        self.assertEqual(self.messages, [1000, 201])
        self.assertEqual((await self.store.config(1))[1]["retired_panels"], [])

    async def test_restart_reconciles_menu_position_without_new_message_event(self):
        self.messages.append(1000)
        await self.store.close()
        self.store = await Store.open(self.path)
        self.make_cog()
        await self.cog.sticky_tick()
        self.assertEqual(self.messages, [1000, 201])
        self.send.assert_awaited_once()

    async def test_channel_change_while_sending_cleans_unsaved_menu(self):
        self.messages.append(1000)

        async def send(channel, view):
            revision, _ = await self.store.config(1)
            await self.store.configure(1, revision, channel=11)
            self.messages.append(201)
            return SimpleNamespace(id=201, delete=self.channel.get_partial_message(201).delete)

        self.send.side_effect = send
        with self.assertRaises(ValueError):
            await self.cog.bump_sticky(1)
        self.assertEqual(self.messages, [100, 1000])
        self.assertEqual((await self.store.config(1))[1]["panel"], 100)

    async def test_listener_debounces_members_but_ignores_its_own_menu(self):
        menu = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=10),
            author=SimpleNamespace(id=999),
            components=[
                SimpleNamespace(to_dict=lambda: {"components": [{"custom_id": "ky:ask:entry"}]})
            ],
        )
        await self.cog.on_message(menu)
        self.assertEqual(self.cog.sticky_due, {})
        menu.author.id = 50
        menu.components = []
        self.messages.append(1000)
        await self.cog.on_message(menu)
        self.assertGreater(self.cog.sticky_due[1], time.monotonic())
        self.cog.sticky_scan = time.monotonic() + 30
        await self.cog.sticky_tick()
        self.send.assert_not_awaited()
        self.cog.sticky_due[1] = 0
        await self.cog.sticky_tick()
        self.send.assert_awaited_once()

    async def test_history_failure_does_not_create_duplicate(self):
        self.channel.history.side_effect = discord.Forbidden(
            SimpleNamespace(status=403, reason="Forbidden"), "No access"
        )
        await self.cog.sticky_tick()
        self.send.assert_not_awaited()
        self.assertIn(1, self.cog.sticky_due)
        self.assertEqual(self.messages, [100])

    async def test_public_request_delivery_leaves_menu_below_the_request(self):
        log_channel = SimpleNamespace(id=20, send=AsyncMock(return_value=SimpleNamespace(id=700)))
        self.cog.channel = AsyncMock(
            side_effect=lambda cid, guild: log_channel if cid == 20 else self.channel
        )
        self.cog.check_channel = AsyncMock()
        self.cog.notify = AsyncMock()
        token = await self.store.draft(1, 50, 60, "dm", "Private reason")
        await self.store.reserve(token, 50, self.revision, ["Sender", "Recipient"])
        await self.cog.process(*(await self.store.events())[0])
        self.assertEqual(self.messages, [201, 202])
        self.assertEqual((await self.store.config(1))[1]["panel"], 202)
        self.assertEqual(await self.store.events(), [])
        self.deletes[100].assert_awaited_once()
        log_channel.send.assert_awaited_once()

    async def test_continuous_messages_do_not_postpone_move_forever(self):
        message = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=10),
            author=SimpleNamespace(id=50),
            components=[],
        )
        await self.cog.on_message(message)
        first_due = self.cog.sticky_due[1]
        await self.cog.on_message(message)
        self.assertEqual(self.cog.sticky_due[1], first_due)


if __name__ == "__main__":
    unittest.main()
