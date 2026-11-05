"""Offline regression coverage: consent ownership, durable state and visibility."""

import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from ky_bot.askdm.command import AskDM, role_allowed
from ky_bot.askdm.store import Store, defaults
from ky_bot.askdm.views import Action, Admin, Confirmation, Form, PublicPanel


class ConsentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "settings.sqlite3"
        self.store = await Store.open(self.path)
        self.revision, self.config = await self.store.configure(
            1, None, enabled=True, channel=10, log_channel=20
        )

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    async def new_request(self, *, guild=1, sender=100, target=200, kind="dm", reason="private"):
        revision, _ = await self.store.config(guild)
        token = await self.store.draft(guild, sender, target, kind, reason)
        rid = await self.store.reserve(token, sender, revision, ["Sender", "Recipient"])
        return token, rid

    async def test_draft_is_private_and_replay_is_idempotent_after_restart(self):
        token = await self.store.draft(1, 100, 200, "dm", "private")
        self.assertEqual(await self.store.events(), [])
        await self.store.close()
        self.store = await Store.open(self.path)
        ids = await asyncio.gather(
            *[
                self.store.reserve(token, 100, self.revision, ["Sender", "Recipient"])
                for _ in range(3)
            ]
        )
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(await self.store.events()), 1)
        with self.assertRaises(ValueError):
            await self.store.reserve(token, 999, self.revision, ["Other", "Recipient"])

    async def test_atomic_competing_decisions_and_revocation(self):
        _, rid = await self.new_request()
        with self.assertRaises(ValueError):
            await self.store.transition(rid, 100, "accepted")
        results = await asyncio.gather(
            self.store.transition(rid, 200, "accepted"),
            self.store.transition(rid, 200, "denied"),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(r, ValueError) for r in results), 1)
        self.assertEqual((await self.store.get(rid))["state"], "accepted")
        await self.store.transition(rid, 200, "revoked")
        self.assertEqual((await self.store.get(rid))["state"], "revoked")
        await self.new_request()  # Revoked consent does not prevent a future request.

    async def test_duplicates_cancel_expiry_and_server_isolation(self):
        _, rid = await self.new_request()
        with self.assertRaises(ValueError):
            await self.new_request()
        with self.assertRaises(ValueError):
            await self.store.transition(rid, 200, "cancelled")
        await self.store.transition(rid, 100, "cancelled")
        _, another = await self.new_request()
        async with self.store.lock:
            await self.store.execute(
                "UPDATE ask_requests SET created=? WHERE id=?", (time.time() - 172801, another)
            )
        await self.store.expire()
        self.assertEqual((await self.store.get(another))["state"], "expired")
        await self.store.configure(2, None, enabled=True, channel=30, log_channel=40)
        await self.new_request(guild=2)
        self.assertEqual(await self.store.inbox(1, 100), [])
        self.assertEqual(len(await self.store.inbox(2, 100)), 1)

    async def test_settings_conflict_cannot_submit_old_draft(self):
        token = await self.store.draft(1, 100, 200, "dm", "")
        await self.store.configure(1, self.revision, sender_roles=[500])
        with self.assertRaises(ValueError):
            await self.store.reserve(token, 100, self.revision, ["S", "R"])
        with self.assertRaises(ValueError):
            await self.store.configure(1, self.revision, public=True)
        self.assertEqual(await self.store.events(), [])

    async def test_confirmation_cancel_and_expiration(self):
        token = await self.store.draft(1, 100, 200, "dm", "")
        await self.store.cancel_draft(token, 999)
        self.assertIsNotNone(await self.store.get_draft(token))
        await self.store.cancel_draft(token, 100)
        with self.assertRaises(ValueError):
            await self.store.reserve(token, 100, self.revision, ["S", "R"])
        token = await self.store.draft(1, 100, 200, "dm", "")
        async with self.store.lock:
            await self.store.execute(
                "UPDATE ask_drafts SET created=? WHERE id=?", (time.time() - 901, token)
            )
        with self.assertRaises(ValueError):
            await self.store.reserve(token, 100, self.revision, ["S", "R"])

    async def test_hidden_requests_deliver_and_log_without_public_post(self):
        _, rid = await self.new_request()
        cog = AskDM(Mock())
        cog.store = self.store
        channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=700)))
        cog.channel = AsyncMock(return_value=channel)
        cog.check_channel = AsyncMock()
        cog.notify = AsyncMock()
        cog.send_panel = AsyncMock()
        await cog.process(*(await self.store.events())[0])
        channel.send.assert_awaited_once()
        self.assertEqual(cog.notify.await_count, 2)
        cog.send_panel.assert_not_awaited()
        self.assertEqual(await self.store.events(), [])
        # Confirm a shown request gets a public card, with no private reason in it.
        revision, _ = await self.store.config(1)
        await self.store.configure(1, revision, public=True)
        await self.new_request(target=201)
        channel.id = 10
        cog.send_panel.return_value = SimpleNamespace(id=701)
        await cog.process(*(await self.store.events())[0])
        view = cog.send_panel.await_args.args[1]
        payload = json.dumps(view.to_components())
        self.assertNotIn('"content": "**PRIVATE REASON**', payload)
        self.assertNotIn("private", payload.lower())

    async def test_blocked_dms_fall_back_only_to_private_thread(self):
        _, rid = await self.new_request()
        request = await self.store.get(rid)
        eid = (await self.store.events())[0][0]
        member = SimpleNamespace(id=200)
        guild = SimpleNamespace(fetch_member=AsyncMock(return_value=member))
        cog = AskDM(SimpleNamespace(get_guild=Mock(return_value=guild), get_channel=Mock()))
        cog.store = self.store
        thread = SimpleNamespace(id=55, archived=False, add_user=AsyncMock())
        channel = SimpleNamespace(create_thread=AsyncMock(return_value=thread))
        cog.channel = AsyncMock(return_value=channel)
        error = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "Closed DMs")
        cog.send_panel = AsyncMock(
            side_effect=[error, SimpleNamespace(id=77, channel=SimpleNamespace(id=55))]
        )
        receipt = {}
        await cog.notify(eid, request, json.loads(request["data"]), 200, receipt)
        self.assertEqual(
            channel.create_thread.await_args.kwargs["type"], discord.ChannelType.private_thread
        )
        self.assertFalse(channel.create_thread.await_args.kwargs["invitable"])
        thread.add_user.assert_awaited_once_with(member)
        self.assertEqual(receipt["thread:200"], 55)
        self.assertEqual(receipt["200"]["message"], 77)

    async def test_failed_delivery_stays_queued_and_receipts_survive(self):
        _, rid = await self.new_request()
        cog = AskDM(Mock())
        cog.store = self.store
        channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=700)))
        cog.channel = AsyncMock(return_value=channel)
        cog.check_channel = AsyncMock()
        cog.notify = AsyncMock(side_effect=ValueError("temporary failure"))
        with self.assertRaises(ValueError):
            await cog.process(*(await self.store.events())[0])
        self.assertEqual(await self.store.pending_count(), 1)
        cog.notify = AsyncMock()
        await cog.process(*(await self.store.events())[0])
        channel.send.assert_awaited_once()  # Saved log receipt prevents a repeated log.
        self.assertEqual(await self.store.pending_count(), 0)

    async def test_hide_removes_existing_public_card(self):
        _, rid = await self.new_request()
        event = (await self.store.events())[0]
        await self.store.checkpoint(event[0], {"public": {"channel": 10, "message": 88}}, True)
        message = SimpleNamespace(delete=AsyncMock())
        channel = SimpleNamespace(fetch_message=AsyncMock(return_value=message))
        cog = AskDM(Mock())
        cog.store = self.store
        cog.channel = AsyncMock(return_value=channel)
        await cog.refresh_cards()
        message.delete.assert_awaited_once()
        await cog.refresh_cards()
        message.delete.assert_awaited_once()

    async def test_role_allow_and_block_precedence(self):
        member = SimpleNamespace(roles=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
        config = defaults()
        self.assertTrue(role_allowed(member, config, "sender"))
        config["sender_roles"] = [3]
        self.assertFalse(role_allowed(member, config, "sender"))
        config["sender_roles"] = [2]
        self.assertTrue(role_allowed(member, config, "sender"))
        config["blocked_sender_roles"] = [1]
        self.assertFalse(role_allowed(member, config, "sender"))
        self.assertTrue(role_allowed(member, config, "recipient"))

    async def test_all_menu_pages_fit_discord_component_limits(self):
        parent = SimpleNamespace(panel_manager=None, guild_id=1, interaction_check=AsyncMock())
        cog = AskDM(Mock())
        for picker in (None, "channel", "log_channel", "roles", *Admin.ROLE_FIELDS):
            view = Admin(cog, parent, self.revision, self.config, picker=picker)
            self.assertLessEqual(view.total_children_count, 40)
            self.assertLessEqual(view.content_length(), 4000)
            view.to_components()
            view.stop()
        for view in (PublicPanel(cog), Form(cog, 100, 1), Confirmation("a" * 32, 200, "dm")):
            self.assertLessEqual(view.total_children_count, 40)
            view.stop()
        action = Action("a" * 32, "accept")
        self.assertIsNotNone(action.template.fullmatch(action.custom_id))

    async def test_nonparticipant_cannot_change_request_in_another_server(self):
        _, rid = await self.new_request()
        cog = AskDM(Mock())
        cog.store = self.store
        i = SimpleNamespace(user=SimpleNamespace(id=999), guild_id=1)
        with self.assertRaises(ValueError):
            await cog.action(i, rid, "accept")
        i = SimpleNamespace(user=SimpleNamespace(id=200), guild_id=2)
        with self.assertRaises(ValueError):
            await cog.action(i, rid, "accept")
        self.assertEqual((await self.store.get(rid))["state"], "pending")


if __name__ == "__main__":
    unittest.main()
