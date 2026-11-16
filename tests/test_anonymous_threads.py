import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from ky_bot.advice.command import Advice
from ky_bot.advice.store import Store as AdviceStore
from ky_bot.confessions.cards import post_card
from ky_bot.confessions.command import Confessions
from ky_bot.confessions.store import Store as ConfessionStore
from ky_bot.services.anonymous_threads import message_destination, reply_thread


class AnonymousThreadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores = []
        self.permissions = discord.Permissions(
            view_channel=True,
            send_messages=True,
            attach_files=True,
            read_message_history=True,
            create_public_threads=True,
            send_messages_in_threads=True,
        )
        self.guild = SimpleNamespace(
            id=1, me=Mock(), get_thread=Mock(return_value=None), fetch_channel=AsyncMock()
        )
        self.thread = Mock(spec=discord.Thread)
        self.thread.id, self.thread.parent_id, self.thread.guild = 500, 10, self.guild
        self.thread.archived, self.thread.locked = False, False
        self.thread.permissions_for = Mock(return_value=self.permissions)
        self.thread.edit = AsyncMock(return_value=self.thread)
        self.message = SimpleNamespace(create_thread=AsyncMock(return_value=self.thread))
        self.channel = SimpleNamespace(
            id=10,
            permissions_for=Mock(return_value=self.permissions),
            get_partial_message=Mock(return_value=self.message),
        )
        self.missing = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), "Missing")

    async def asyncTearDown(self):
        for store in self.stores:
            await store.close()

    async def root(self, advice=False, approval=False):
        store = await (AdviceStore if advice else ConfessionStore).open(":memory:")
        self.stores.append(store)
        changes = dict(enabled=True, cooldown=0, queue_channel=20)
        if advice:
            changes.update(
                access_channel=11, access_role=30, advice_role=40, response_approval=approval
            )
        else:
            changes["approval"] = approval
        revision, config = await store.configure(1, 10, None, **changes)
        post = await store.submit(1, 10, 100, [30], "Root post", 1, revision)
        await store.transition(post["id"], {post["state"]}, "published", message=500)
        return store, revision, config, await store.post(post["id"])

    async def test_original_authorship_without_logs_and_replies_to_replies_in_both_features(self):
        for advice in (False, True):
            with self.subTest(advice=advice):
                store, revision, _, root = await self.root(advice)
                self.assertIsNone(root["author"])
                self.assertTrue(await store.is_original(1, 10, root["id"], 100))
                self.assertFalse(await store.is_original(1, 10, root["id"], 101))
                other = await store.submit(
                    1, 10, 101, [30, 40], "First reply", 2, revision, parent=root["id"]
                )
                self.assertFalse(other["data"]["is_original_poster"])
                await store.transition(
                    other["id"], {"ready"}, "published", message=600, message_channel=500
                )
                answer = await store.submit(
                    1, 10, 100, [30], "Thank you", 3, revision, parent=other["id"]
                )
                self.assertTrue(answer["data"]["is_original_poster"])
                self.assertEqual(answer["parent"], root["id"])
                self.assertEqual(answer["data"]["reply_to"], other["id"])
                self.assertIsNone(answer["author"])

    async def test_approval_still_required_for_original_posters(self):
        for advice in (False, True):
            store, revision, _, root = await self.root(advice, approval=True)
            reply = await store.submit(
                1, 10, 100, [30], "My follow-up", 2, revision, parent=root["id"]
            )
            self.assertEqual(reply["state"], "pending")
            self.assertTrue(reply["data"]["is_original_poster"])

    async def test_create_once_and_recover_thread_across_restart(self):
        store, _, _, root = await self.root()
        self.guild.fetch_channel.side_effect = self.missing
        first = await reply_thread(self.guild, self.channel, root, store, label="Confession")
        self.assertIs(first, self.thread)
        self.message.create_thread.assert_awaited_once()
        saved = await store.post(root["id"])
        self.assertEqual(saved["data"]["thread"], 500)
        self.guild.fetch_channel.side_effect = None
        self.guild.fetch_channel.return_value = self.thread
        second = await reply_thread(self.guild, self.channel, saved, store, label="Confession")
        self.assertIs(second, self.thread)
        self.message.create_thread.assert_awaited_once()
        self.thread.add_user.assert_not_called()

    async def test_archived_threads_reopen_but_locked_threads_do_not(self):
        store, _, _, root = await self.root()
        self.guild.get_thread.return_value = self.thread
        self.thread.archived = True
        await reply_thread(self.guild, self.channel, root, store, label="Question")
        self.thread.edit.assert_awaited_once_with(
            archived=False, reason="New approved anonymous reply"
        )
        self.thread.edit.reset_mock()
        self.thread.locked = True
        with self.assertRaisesRegex(ValueError, "locked"):
            await reply_thread(self.guild, self.channel, root, store, label="Question")
        self.thread.edit.assert_not_awaited()

    async def test_missing_permissions_and_foreign_threads_rejected(self):
        store, _, _, root = await self.root()
        self.permissions.send_messages_in_threads = False
        with self.assertRaisesRegex(ValueError, "Send Messages in Threads"):
            await reply_thread(self.guild, self.channel, root, store, label="Question")
        self.permissions.send_messages_in_threads = True
        self.guild.get_thread.return_value = self.thread
        self.thread.parent_id = 999
        with self.assertRaisesRegex(ValueError, "does not belong"):
            await reply_thread(self.guild, self.channel, root, store, label="Question")

    async def test_both_features_publish_replies_inside_original_thread(self):
        for advice in (False, True):
            with self.subTest(advice=advice):
                store, revision, config, root = await self.root(advice)
                reply = await store.submit(
                    1, 10, 100, [30], "OP reply", 2, revision, parent=root["id"]
                )
                cog = Advice(SimpleNamespace()) if advice else Confessions(SimpleNamespace())
                cog.store = store
                module = "ky_bot.advice.command" if advice else "ky_bot.confessions.command"
                sender = AsyncMock(return_value=SimpleNamespace(id=601))
                if advice:
                    cog.send_panel = sender
                    cog.find_delivery = AsyncMock(return_value=None)
                else:
                    cog.send_card = sender
                    cog.refresh_queue = AsyncMock()
                with (
                    patch(module + ".destination", return_value=self.channel),
                    patch(module + ".reply_thread", new=AsyncMock(return_value=self.thread)),
                    patch(
                        module + (".post_panel" if advice else ".post_card"), return_value=Mock()
                    ),
                ):
                    if advice:
                        await cog.deliver(self.guild, reply)
                    else:
                        await cog.publish(self.guild, reply, config)
                self.assertIs(sender.await_args.args[0], self.thread)
                stored = await store.post(reply["id"])
                self.assertEqual(stored["data"]["message_channel"], 500)
                self.assertTrue(stored["data"]["is_original_poster"])

    async def test_op_replies_reference_the_advice_being_answered(self):
        store, revision, _, root = await self.root(advice=True)
        response = await store.submit(
            1, 10, 101, [30, 40], "Advice", 2, revision, parent=root["id"]
        )
        await store.transition(
            response["id"], {"ready"}, "published", message=600, message_channel=500
        )
        reply = await store.submit(1, 10, 100, [30], "Thanks", 3, revision, parent=response["id"])
        cog = Advice(SimpleNamespace())
        cog.store = store
        cog.send_panel = AsyncMock(return_value=SimpleNamespace(id=601))
        cog.find_delivery = AsyncMock(return_value=None)
        reference = SimpleNamespace(id=600)
        self.thread.get_partial_message.return_value = reference
        with (
            patch("ky_bot.advice.command.destination", return_value=self.channel),
            patch("ky_bot.advice.command.reply_thread", new=AsyncMock(return_value=self.thread)),
            patch("ky_bot.advice.command.post_panel", return_value=Mock()),
        ):
            await cog.deliver(self.guild, reply)
        self.assertIs(cog.send_panel.await_args.kwargs["reference"], reference)

    async def test_pending_replies_do_not_create_thread(self):
        store, revision, _, root = await self.root(advice=True, approval=True)
        reply = await store.submit(1, 10, 100, [30], "Follow-up", 2, revision, parent=root["id"])
        cog = Advice(SimpleNamespace())
        cog.store = store
        cog.send_panel = AsyncMock(return_value=SimpleNamespace(id=601))
        cog.find_delivery = AsyncMock(return_value=None)
        with (
            patch("ky_bot.advice.command.destination", return_value=self.channel),
            patch("ky_bot.advice.command.reply_thread", new=AsyncMock()) as create,
            patch("ky_bot.advice.command.post_panel", return_value=Mock()),
        ):
            await cog.deliver(self.guild, reply)
        create.assert_not_awaited()
        self.assertEqual((await store.post(reply["id"]))["state"], "pending")

    async def test_blocked_confession_thread_keeps_reply_reviewable_without_publishing(self):
        store, revision, config, root = await self.root()
        reply = await store.submit(1, 10, 100, [], "Follow-up", 2, revision, parent=root["id"])
        cog = Confessions(SimpleNamespace())
        cog.store = store
        cog.send_card = AsyncMock()
        with (
            patch("ky_bot.confessions.command.destination", return_value=self.channel),
            patch(
                "ky_bot.confessions.command.reply_thread",
                new=AsyncMock(side_effect=ValueError("locked")),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "saved in Pending approvals"):
                await cog.publish(self.guild, reply, config)
        self.assertEqual((await store.post(reply["id"]))["state"], "pending")
        cog.send_card.assert_not_awaited()

    async def test_reply_moderation_uses_thread_and_op_label_is_public_without_identity(self):
        store, revision, config, root = await self.root()
        reply = await store.submit(1, 10, 100, [], "Follow-up", 2, revision, parent=root["id"])
        await store.transition(
            reply["id"], {"ready"}, "published", message=600, message_channel=500
        )
        reply = await store.post(reply["id"])
        self.guild.get_channel = Mock(return_value=self.thread)
        self.assertIs(await message_destination(self.guild, reply), self.thread)
        with (
            patch("ky_bot.confessions.cards.standard_artwork", return_value=b"art"),
            patch("ky_bot.confessions.cards.title_image", return_value=b"heading"),
        ):
            card = post_card(config, reply)
        contents = "\n".join(
            item.content
            for item in card.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("Original Poster", contents)
        self.assertNotIn("<@100>", contents)
        self.assertIn("/500/600", contents)


if __name__ == "__main__":
    unittest.main()
