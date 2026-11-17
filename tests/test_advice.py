import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from ky_bot.advice.command import Advice, Confirm
from ky_bot.advice.menus import Admin
from ky_bot.advice.panels import access_panel, artwork_bytes, post_panel, submission_panel
from ky_bot.advice.store import Store, defaults, validate


class AdviceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Exercise real component trees without repeatedly regenerating shared brand artwork.
        for target in (
            "ky_bot.advice.panels.standard_artwork",
            "ky_bot.advice.panels.heading_image",
        ):
            patcher = patch(target, return_value=b"image-placeholder")
            patcher.start()
            self.addCleanup(patcher.stop)
        self.store = await Store.open(":memory:")
        self.revision, self.config = await self.store.configure(
            1,
            10,
            None,
            enabled=True,
            cooldown=0,
            access_channel=20,
            access_role=30,
            advice_role=40,
            queue_channel=50,
            log_channel=60,
        )

    async def asyncTearDown(self):
        await self.store.close()

    async def configure(self, **changes):
        self.revision, self.config = await self.store.configure(1, 10, self.revision, **changes)

    async def submit(self, operation=1, roles=None, **kwargs):
        return await self.store.submit(
            1,
            10,
            100,
            [30] if roles is None else roles,
            "A question or response",
            operation,
            self.revision,
            **kwargs,
        )

    async def published_question(self):
        post = await self.submit()
        await self.store.transition(post["id"], {"ready"}, "published", message=555)
        return await self.store.post(post["id"])

    async def test_independent_approval_combinations_and_multiple_responses(self):
        question = await self.published_question()
        for index, (questions, responses) in enumerate(
            ((False, False), (False, True), (True, False), (True, True))
        ):
            await self.configure(approval=questions, response_approval=responses)
            root = await self.submit(operation=10 + index)
            self.assertEqual(root["state"], "pending" if questions else "ready")
            reply = await self.submit(operation=20 + index, roles=[30, 40], parent=question["id"])
            self.assertEqual(reply["state"], "pending" if responses else "ready")
            self.assertEqual(reply["parent"], question["id"])
        rows = await self.store.execute(
            "SELECT COUNT(*) FROM advice_posts WHERE parent=?", (question["id"],)
        )
        self.assertEqual(rows[0], 4)

    async def test_members_need_access_and_responder_roles(self):
        question = await self.published_question()
        for roles in ([], [40]):
            with self.assertRaisesRegex(ValueError, "Join"):
                await self.submit(operation=2, roles=roles)
        with self.assertRaisesRegex(ValueError, "Give Advice"):
            await self.store.submit(
                1, 10, 101, [30], "Advice", 3, self.revision, parent=question["id"]
            )
        own_reply = await self.submit(operation=4, parent=question["id"])
        self.assertTrue(own_reply["data"]["is_original_poster"])

    async def test_anonymous_questions_and_optional_named_responses(self):
        question = await self.published_question()
        self.assertIsNone(question["author"])
        self.assertIsNone(question["data"]["public_author"])
        for operation, named in ((2, False), (3, True)):
            response = await self.submit(
                operation, roles=[30, 40], parent=question["id"], named=named
            )
            await self.store.transition(response["id"], {"ready"}, "published")
            response = await self.store.post(response["id"])
            self.assertIsNone(response["author"])
            self.assertEqual(response["data"]["public_author"], 100 if named else None)

    async def test_privacy_revision_prevents_silent_change(self):
        old = self.revision
        await self.configure(logging=True)
        with self.assertRaisesRegex(ValueError, "privacy"):
            await self.store.submit(1, 10, 100, [30], "Question", 1, old)

    async def test_review_identity_removed_after_publication_when_logs_off(self):
        await self.configure(approval=True)
        post = await self.submit()
        self.assertEqual(post["author"], 100)
        await self.store.transition(post["id"], {"pending"}, "ready")
        await self.store.transition(post["id"], {"ready"}, "published")
        self.assertIsNone((await self.store.post(post["id"]))["author"])
        with self.assertRaises(ValueError):
            await self.store.transition(post["id"], {"pending"}, "ready")

    async def test_cooldown_and_idempotent_operation(self):
        await self.configure(cooldown=60)
        first = await self.submit()
        self.assertEqual((await self.submit())["id"], first["id"])
        with self.assertRaisesRegex(ValueError, "wait"):
            await self.submit(2)

    async def test_unpublished_and_cross_channel_parents_rejected(self):
        root = await self.submit()
        with self.assertRaisesRegex(ValueError, "original post"):
            await self.submit(2, roles=[30, 40], parent=root["id"])

    async def test_settings_validation(self):
        for changes in (
            dict(access_role=40),
            dict(access_channel=10),
            dict(response_approval=True, queue_channel=0),
            dict(logging=True, log_channel=0),
            dict(sticky_count=0),
        ):
            with self.assertRaises(ValueError):
                await self.configure(**changes)
        changed = copy.deepcopy(defaults())
        changed["sections"].append(
            dict(
                heading="EXTRA",
                decorated=False,
                rows=[dict(action="join", label="Duplicate", emoji="", text="Text")],
            )
        )
        with self.assertRaisesRegex(ValueError, "only once"):
            validate(changed)

    async def test_separate_tables_and_sticky_persistence(self):
        await self.submit()
        self.assertIsNone(
            await self.store.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                ("confession_posts",),
            )
        )
        await self.store.panel(1, 10, message=99)
        for _ in range(5):
            await self.store.panel(1, 10, count=True)
        self.assertEqual((await self.store.panel(1, 10))[:2], (99, 5))

    async def test_four_right_hand_buttons_and_group_divider(self):
        view = access_panel(10, self.config)
        sections = [item for item in view.walk_children() if isinstance(item, discord.ui.Section)]
        self.assertEqual(
            [s.accessory.action for s in sections], ["join", "leave", "give", "ungive"]
        )
        separators = [
            item for item in view.panel.children if isinstance(item, discord.ui.Separator)
        ]
        self.assertEqual(separators[0].spacing, discord.SeparatorSpacing.large)
        self.assertLessEqual(view.total_children_count, 40)
        view.to_components()
        preview = access_panel(10, self.config, preview=True)
        self.assertTrue(
            all(item.item.disabled for item in preview.walk_children() if hasattr(item, "action"))
        )
        submission_panel(10, self.config).to_components()
        root = await self.published_question()
        post_panel(root, self.config).to_components()

    async def test_all_settings_pages_fit_discord_limits(self):
        cog = SimpleNamespace(store=self.store)
        menu = Admin(cog, 7, 1)
        menu.channel = 10
        menu.allowed = AsyncMock()
        interaction = SimpleNamespace(guild_id=1)
        for page in (
            "channels",
            "main",
            "roles",
            "delivery",
            "destinations",
            "style",
            "artwork",
            "header_artwork",
            "footer_artwork",
            "sections",
            "section",
            "row",
            "publish",
            "queue",
        ):
            with patch("ky_bot.advice.menus.display_menu", new=AsyncMock()):
                await menu.show(interaction, page)
            self.assertLessEqual(menu.total_children_count, 40, page)
            menu.to_components()
        menu.stop()

    async def test_every_settings_page_uses_shared_category_artwork_and_spacing(self):
        cog = SimpleNamespace(store=self.store)
        for parent in (None, SimpleNamespace(panel_manager=None)):
            menu = Admin(cog, 7, 1, parent=parent)
            menu.channel = 10
            menu.allowed = AsyncMock()
            interaction = SimpleNamespace(guild_id=1)
            for page in (
                "channels",
                "main",
                "roles",
                "delivery",
                "destinations",
                "style",
                "artwork",
                "header_artwork",
                "footer_artwork",
                "sections",
                "section",
                "row",
                "publish",
                "queue",
            ):
                with patch("ky_bot.advice.menus.display_menu", new=AsyncMock()) as display:
                    await menu.show(interaction, page, "Saved settings.")
                self.assertLessEqual(menu.total_children_count, 40, page)
                media = {
                    item.media.url.removeprefix("attachment://")
                    for child in menu.walk_children()
                    if isinstance(child, discord.ui.MediaGallery)
                    for item in child.items
                }
                files = display.await_args.args[2]
                self.assertTrue(media <= {file.filename for file in files}, page)
                self.assertTrue(any(name.startswith("ky_strip_") for name in media), page)
                self.assertNotIn("KY BOT · Ask for Advice", str(menu.to_components()), page)
                if page == "main":
                    first = next(
                        item
                        for item in menu.walk_children()
                        if isinstance(item, discord.ui.Section)
                        and "CHANNELS & ROLES" in item.children[0].content
                    )
                    self.assertTrue(first.children[0].content.endswith("\n-# \u200b"))
            menu.stop()

    async def test_artwork_editors_separate_images_and_explain_title_controls(self):
        menu = Admin(SimpleNamespace(store=self.store), 7, 1)
        menu.channel = 10
        menu.allowed = AsyncMock()
        i = SimpleNamespace(guild_id=1, response=SimpleNamespace(send_modal=AsyncMock()))
        with patch("ky_bot.advice.menus.display_menu", new=AsyncMock()):
            await menu.show(i, "artwork")
            labels = {
                item.label for item in menu.walk_children() if isinstance(item, discord.ui.Button)
            }
            self.assertIn("Edit header", labels)
            self.assertIn("Edit footer", labels)
            await menu.show(i, "header_artwork")
        self.assertNotIn("Follow decorative text", str(menu.to_components()))
        color = next(
            item
            for item in menu.walk_children()
            if isinstance(item, discord.ui.Section)
            and "**TITLE COLOR**" in item.children[0].content
        )
        await color.accessory.callback(i)
        modal = i.response.send_modal.await_args.args[0]
        self.assertEqual(set(modal.fields), {"header_text_color"})
        menu.stop()

    async def test_artwork_uses_persisted_custom_palettes_and_independent_title_color(self):
        await self.configure(
            header_palette={"base": "#7B2CBF"},
            footer_palette={"base": "#22CC88"},
            header_text_color="#FFAA00",
            header_text_finish="gold",
        )
        config = (await self.store.config(1, 10))[1]
        with patch("ky_bot.advice.panels.standard_artwork", return_value=b"art") as render:
            artwork_bytes(config, "header")
            self.assertEqual(render.call_args.args[3:5], ("gold", "#FFAA00"))
            self.assertEqual(render.call_args.args[5][0], "#7B2CBF")
            artwork_bytes(config, "footer")
            self.assertEqual(render.call_args.args[5][0], "#22CC88")

    async def test_old_default_footer_is_hidden_but_custom_footer_is_kept(self):
        config = self.config | {"footer_text": "KY BOT · Ask for Advice"}
        self.assertNotIn("KY BOT · Ask for Advice", str(access_panel(10, config).to_components()))
        config["footer_text"] = "My community footer"
        self.assertIn("My community footer", str(access_panel(10, config).to_components()))

    async def test_role_buttons_enforce_dependencies_and_leave_removes_both(self):
        await self.store.panel(1, 20, message=777)
        member = SimpleNamespace(roles=[], add_roles=AsyncMock(), remove_roles=AsyncMock())
        guild = SimpleNamespace(id=1, fetch_member=AsyncMock(return_value=member))
        i = SimpleNamespace(
            guild_id=1,
            guild=guild,
            user=SimpleNamespace(id=100),
            channel_id=20,
            message=SimpleNamespace(id=777),
        )
        cog = Advice(SimpleNamespace())
        cog.store = self.store
        access, advice = SimpleNamespace(id=30), SimpleNamespace(id=40)
        with (
            patch("ky_bot.advice.command.roles_for", return_value=[access, advice]),
            patch("ky_bot.advice.command.send_private", new=AsyncMock()),
        ):
            with self.assertRaisesRegex(ValueError, "Join"):
                await cog.role_action(i, 10, "give")
            member.roles = [access]
            await cog.role_action(i, 10, "give")
            member.add_roles.assert_awaited_once_with(
                advice, reason="Ask for Advice: opt in to advice"
            )
            await cog.role_action(i, 10, "ungive")
            member.remove_roles.assert_awaited_once_with(
                advice, reason="Ask for Advice: stop giving advice"
            )
            member.remove_roles.reset_mock()
            await cog.role_action(i, 10, "leave")
            self.assertEqual(
                [call.args[0].id for call in member.remove_roles.await_args_list], [40, 30]
            )

    async def test_counts_bot_and_member_messages_but_not_panel(self):
        cog = Advice(SimpleNamespace(user=SimpleNamespace(id=999)))
        cog.store = self.store
        await self.store.panel(1, 10, message=777)
        for message_id, author_id in ((777, 999), (778, 100), (779, 999)):
            await cog.on_message(
                SimpleNamespace(
                    guild=SimpleNamespace(id=1),
                    channel=SimpleNamespace(id=10),
                    id=message_id,
                    author=SimpleNamespace(id=author_id),
                )
            )
        self.assertEqual((await self.store.panel(1, 10))[1], 2)

    async def test_delivery_reuses_existing_public_message_and_notifies_once(self):
        await self.configure(notifications=True)
        post = await self.submit()
        cog = Advice(SimpleNamespace())
        cog.store = self.store
        cog.find_delivery = AsyncMock(side_effect=[SimpleNamespace(id=1234), None])
        cog.send_panel = AsyncMock()
        target = SimpleNamespace(id=10, send=AsyncMock())
        guild = SimpleNamespace(
            id=1, get_role=Mock(return_value=SimpleNamespace(id=40, mentionable=True))
        )
        with patch("ky_bot.advice.command.destination", return_value=target):
            await cog.deliver(guild, post)
            await cog.deliver(guild, post)
        cog.send_panel.assert_not_awaited()
        target.send.assert_awaited_once()
        mentions = target.send.await_args.kwargs["allowed_mentions"]
        self.assertEqual([role.id for role in mentions.roles], [40])
        saved = await self.store.post(post["id"])
        self.assertEqual(saved["state"], "published")
        self.assertTrue(saved["data"]["notified"])

    async def test_saved_settings_pending_replies_and_panel_counts_survive_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "advice.sqlite3"
            store = await Store.open(path)
            revision, _ = await store.configure(1, 10, None, **self.config)
            root = await store.submit(1, 10, 100, [30], "Question", 1, revision)
            await store.transition(root["id"], {"ready"}, "published", message=123)
            revision, _ = await store.configure(1, 10, revision, response_approval=True)
            reply = await store.submit(
                1, 10, 101, [30, 40], "Advice", 2, revision, parent=root["id"]
            )
            await store.panel(1, 10, message=777)
            await store.panel(1, 10, count=True)
            await store.close()
            store = await Store.open(path)
            try:
                self.assertEqual((await store.post(reply["id"]))["state"], "pending")
                self.assertTrue((await store.config(1, 10))[1]["response_approval"])
                self.assertEqual((await store.panel(1, 10))[:2], (777, 1))
            finally:
                await store.close()

    async def test_maximum_length_confirmations_and_author_logs_use_components(self):
        form = SimpleNamespace(parent=None)
        view = Confirm(form, "x" * 2000, "Privacy notice")
        self.assertEqual(len(view.children[1].content), 2000)
        view.to_components()
        self.assertNotIn(
            "Show My Name",
            [item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)],
        )
        await self.configure(logging=True)
        post = await self.submit()
        cog = Advice(SimpleNamespace())
        cog.store = self.store
        cog.find_delivery = AsyncMock(side_effect=[SimpleNamespace(id=1), None])
        target = SimpleNamespace(id=10, send=AsyncMock(return_value=SimpleNamespace(id=2)))
        with patch("ky_bot.advice.command.destination", return_value=target):
            await cog.deliver(SimpleNamespace(id=1), post)
        self.assertIn("view", target.send.await_args.kwargs)
        target.send.await_args.kwargs["view"].to_components()

    async def test_notifications_off_does_not_ping_old_questions_when_enabled_later(self):
        post = await self.submit()
        cog = Advice(SimpleNamespace())
        cog.store = self.store
        cog.find_delivery = AsyncMock(return_value=SimpleNamespace(id=123))
        target = SimpleNamespace(id=10, send=AsyncMock())
        with patch("ky_bot.advice.command.destination", return_value=target):
            await cog.deliver(SimpleNamespace(id=1), post)
            await self.configure(notifications=True)
            await cog.deliver(SimpleNamespace(id=1), post)
        target.send.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
