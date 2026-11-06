import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from ky_bot.confessions.cards import post_card, privacy
from ky_bot.confessions.command import Confessions, administrator
from ky_bot.confessions.menus import Admin
from ky_bot.confessions.store import Store, defaults, validate


class ConfessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = await Store.open(":memory:")
        self.revision, self.config = await self.store.configure(
            1, 10, None, enabled=True, cooldown=0, queue_channel=20
        )

    async def asyncTearDown(self):
        await self.store.close()

    async def submit(self, operation=1, author=123, **kwargs):
        return await self.store.submit(
            1, 10, author, [], "A private thought", operation, self.revision, **kwargs
        )

    async def configure(self, **values):
        self.revision, self.config = await self.store.configure(1, 10, self.revision, **values)

    async def test_logging_destinations_can_be_saved_independently(self):
        await self.configure(queue_channel=0)
        menu = Admin(SimpleNamespace(store=self.store), 7, 1)
        menu.channel = 10
        menu.allowed = AsyncMock()
        interaction = SimpleNamespace(guild=SimpleNamespace())
        with patch("ky_bot.confessions.menus.destination") as check:
            await menu.save(interaction, self.revision, log_channel=30)
            self.assertEqual([call.args[1] for call in check.call_args_list], [10, 30])
            self.assertEqual(menu.config["queue_channel"], 0)
            self.assertEqual(menu.config["log_channel"], 30)
            check.reset_mock()
            await menu.save(interaction, menu.revision, logging=True)
            self.assertEqual([call.args[1] for call in check.call_args_list], [10, 30])
            check.reset_mock()
            await menu.save(interaction, menu.revision, queue_channel=40)
            self.assertEqual([call.args[1] for call in check.call_args_list], [10, 40])
        menu.stop()

    async def test_enabling_logs_requires_named_destination_and_private_checks(self):
        await self.configure(queue_channel=0)
        menu = Admin(SimpleNamespace(store=self.store), 7, 1)
        menu.channel = 10
        menu.allowed = AsyncMock()
        interaction = SimpleNamespace(guild=SimpleNamespace())
        with patch("ky_bot.confessions.menus.destination"):
            for values, label in (
                (dict(logging=True), "Author log channel"),
                (dict(approval=True), "Approval & report channel"),
            ):
                with self.assertRaisesRegex(ValueError, label):
                    await menu.save(interaction, self.revision, **values)
        with patch(
            "ky_bot.confessions.menus.destination",
            side_effect=[None, ValueError("must be private")],
        ):
            with self.assertRaisesRegex(ValueError, "Author log channel: must be private"):
                await menu.save(interaction, self.revision, log_channel=30)
        _, config = await self.store.config(1, 10)
        self.assertEqual(config["log_channel"], 0)
        menu.stop()

    async def test_log_destinations_can_be_removed_and_replaced_from_selector(self):
        await self.configure(
            logging=True, log_channel=30, approval=True, reports=True, queue_channel=20
        )
        menu = Admin(SimpleNamespace(store=self.store), 7, 1)
        menu.channel = 10
        menu.allowed = AsyncMock()
        permissions = discord.Permissions(administrator=True)
        interaction = SimpleNamespace(
            guild=SimpleNamespace(
                fetch_member=AsyncMock(return_value=SimpleNamespace(guild_permissions=permissions))
            ),
            guild_id=1,
            user=SimpleNamespace(id=7),
            response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        menu.show = AsyncMock(wraps=menu.show)
        await menu.show(interaction, "logging")
        picker = next(
            item
            for item in menu.walk_children()
            if isinstance(item, discord.ui.ChannelSelect)
            and item.placeholder == "Author log channel"
        )
        self.assertEqual(picker.min_values, 0)
        picker._values = []
        with patch("ky_bot.confessions.menus.destination"):
            await picker.callback(interaction)
        _, config = await self.store.config(1, 10)
        self.assertEqual(config["log_channel"], 0)
        self.assertFalse(config["logging"])
        self.assertTrue(config["approval"])
        picker = next(
            item
            for item in menu.walk_children()
            if isinstance(item, discord.ui.ChannelSelect)
            and item.placeholder == "Author log channel"
        )
        picker._values = [SimpleNamespace(id=40)]
        with patch("ky_bot.confessions.menus.destination"):
            await picker.callback(interaction)
        self.assertEqual(menu.config["log_channel"], 40)
        picker = next(
            item
            for item in menu.walk_children()
            if isinstance(item, discord.ui.ChannelSelect)
            and item.placeholder == "Approval & report channel"
        )
        picker._values = []
        with patch("ky_bot.confessions.menus.destination"):
            await picker.callback(interaction)
        self.assertEqual(menu.config["queue_channel"], 0)
        self.assertFalse(menu.config["approval"])
        self.assertFalse(menu.config["reports"])
        self.assertEqual(menu.config["log_channel"], 40)
        menu.stop()

    async def test_confession_numbers_are_atomic_idempotent_and_per_channel(self):
        posts = await asyncio.gather(*(self.submit(operation=n) for n in range(1, 6)))
        self.assertEqual(sorted(post["data"]["number"] for post in posts), [1, 2, 3, 4, 5])
        first = posts[0]
        self.assertEqual(
            (await self.submit(operation=1))["data"]["number"], first["data"]["number"]
        )
        await self.store.transition(first["id"], {"ready"}, "published", message=999)
        reply = await self.submit(operation=6, parent=first["id"])
        self.assertEqual(reply["data"]["number"], 1)
        self.assertEqual(reply["data"]["parent_number"], first["data"]["number"])
        self.assertEqual((await self.submit(operation=7))["data"]["number"], 6)
        revision, _ = await self.store.configure(1, 11, None, enabled=True, cooldown=0)
        other = await self.store.submit(1, 11, 123, [], "Other channel", 8, revision)
        self.assertEqual(other["data"]["number"], 1)
        card = post_card(self.config, first)
        self.assertIn("ANONYMOUS CONFESSION", str(card.to_components()))
        self.assertNotIn(first["id"][:8].upper(), str(card.to_components()))
        card.stop()

    async def test_existing_posts_get_stable_numbers_on_restart(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "confessions.sqlite3"
            store = await Store.open(path)
            try:
                revision, _ = await store.configure(1, 10, None, enabled=True, cooldown=0)
                posts = [
                    await store.submit(1, 10, 123, [], "Existing post", n, revision) for n in (1, 2)
                ]
                for post in posts:
                    legacy = dict(post["data"])
                    del legacy["number"]
                    await store.execute(
                        "UPDATE confession_posts SET data=? WHERE id=?",
                        (json.dumps(legacy), post["id"]),
                    )
            finally:
                await store.close()
            store = await Store.open(path)
            try:
                self.assertEqual(
                    [(await store.post(post["id"]))["data"]["number"] for post in posts], [1, 2]
                )
                next_post = await store.submit(1, 10, 123, [], "New post", 3, revision)
                self.assertEqual(next_post["data"]["number"], 3)
                self.assertEqual((await store.post(posts[0]["id"]))["id"], posts[0]["id"])
            finally:
                await store.close()

    async def test_confession_iris_is_compact_beside_number(self):
        import io

        from PIL import Image

        from ky_bot.confessions.headings import DIVIDER, title_image

        with Image.open(io.BytesIO(title_image("ANONYMOUS CONFESSION" + DIVIDER + "1"))) as image:
            self.assertEqual(image.height, 60)

    async def test_public_confession_text_is_large_bold_but_staff_text_stays_plain(self):
        post = await self.store.submit(1, 10, 123, [], "First line\nSecond line", 1, self.revision)
        public = post_card(self.config, post)
        staff = post_card(self.config, post, staff=True)
        public_text = [
            item.content
            for item in public.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        ]
        staff_text = [
            item.content
            for item in staff.walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        ]
        self.assertIn("## First line\n## Second line", public_text)
        self.assertIn("First line\nSecond line", staff_text)
        self.assertNotIn("KY BOT · Confessions", str(staff.to_components()))
        post["body"] = "x\n" * 999
        crowded = post_card(self.config, post)
        self.assertLessEqual(crowded.content_length(), 4000)
        public.stop()
        staff.stop()
        crowded.stop()

    async def test_logging_off_does_not_retain_identifying_author(self):
        post = await self.submit()
        self.assertIsNone(post["author"])
        self.assertNotIn("<@123>", str(post_card(self.config, post).to_components()))
        self.assertNotIn(post["author_key"], str(post_card(self.config, post).to_components()))

    async def test_approval_names_author_but_public_post_never_does(self):
        await self.configure(approval=True)
        post = await self.submit()
        self.assertEqual(post["state"], "pending")
        self.assertEqual(post["author"], 123)
        self.assertIn("<@123>", str(post_card(self.config, post, staff=True).to_components()))
        self.assertNotIn("<@123>", str(post_card(self.config, post).to_components()))
        self.assertIn("Administrators", privacy(self.config))
        await self.store.transition(post["id"], {"pending"}, "ready")
        await self.store.transition(post["id"], {"ready"}, "publishing")
        await self.store.transition(post["id"], {"publishing"}, "published", message=999)
        self.assertIsNone((await self.store.post(post["id"]))["author"])

    async def test_identity_logs_keep_author_and_configuration_is_channel_scoped(self):
        await self.configure(logging=True, log_channel=30, color="CAB6DF")
        post = await self.submit()
        self.assertEqual(post["author"], 123)
        self.assertTrue(post["data"]["logging"])
        await self.store.configure(1, 11, None, enabled=True, color="A9CBBF")
        _, other = await self.store.config(1, 11)
        self.assertFalse(other["logging"])
        self.assertEqual(other["color"], "A9CBBF")
        self.assertEqual(
            post_card(self.config, post).children[0].to_component_dict()["accent_color"], 0xCAB6DF
        )
        self.assertEqual(
            post_card(self.config, post, staff=True)
            .children[0]
            .to_component_dict()["accent_color"],
            0xCAB6DF,
        )

    async def test_changed_privacy_configuration_requires_new_confirmation(self):
        revision = self.revision
        await self.configure(approval=True)
        with self.assertRaises(ValueError):
            await self.store.submit(1, 10, 123, [], "Test", 1, revision)
        with self.assertRaises(ValueError):
            await self.store.configure(1, 10, revision, logging=True, log_channel=30)

    async def test_duplicate_interaction_and_concurrent_approval_are_idempotent(self):
        await self.configure(approval=True)
        first, second = await asyncio.gather(self.submit(), self.submit())
        self.assertEqual(first["id"], second["id"])
        results = await asyncio.gather(
            self.store.transition(first["id"], {"pending"}, "ready"),
            self.store.transition(first["id"], {"pending"}, "ready"),
            return_exceptions=True,
        )
        self.assertEqual(sum(isinstance(v, ValueError) for v in results), 1)

    async def test_confession_bans_work_without_identity_logging_and_can_be_lifted(self):
        post = await self.submit()
        await self.store.set_ban(1, 55, "Abuse", token=post["id"])
        with self.assertRaises(ValueError):
            await self.submit(operation=2)
        await self.store.set_ban(1, 55, "", member=123, remove=True)
        await self.submit(operation=3)

    async def test_replies_cannot_cross_channels_or_target_removed_posts(self):
        post = await self.submit()
        await self.store.transition(post["id"], {"ready"}, "published", message=999)
        reply = await self.submit(operation=2, parent=post["id"])
        self.assertEqual(reply["parent"], post["id"])
        revision, _ = await self.store.configure(1, 11, None, enabled=True, cooldown=0)
        with self.assertRaises(ValueError):
            await self.store.submit(1, 11, 123, [], "Reply", 3, revision, parent=post["id"])
        await self.store.transition(post["id"], {"published"}, "removed")
        with self.assertRaises(ValueError):
            await self.submit(operation=4, parent=post["id"])

    async def test_role_access_cooldown_and_disabled_submissions(self):
        await self.configure(allowed_roles=[50], blocked_roles=[60])
        with self.assertRaises(ValueError):
            await self.submit()
        with self.assertRaises(ValueError):
            await self.store.submit(1, 10, 123, [50, 60], "No", 2, self.revision)
        await self.store.submit(1, 10, 123, [50], "Yes", 3, self.revision)
        await self.configure(allowed_roles=[], blocked_roles=[], cooldown=60)
        with self.assertRaises(ValueError):
            await self.submit(operation=4)
        await self.configure(enabled=False)
        with self.assertRaises(ValueError):
            await self.submit(operation=5, author=500)

    async def test_reports_are_saved_once_and_panels_keep_a_recovery_list(self):
        post = await self.submit()
        await self.store.transition(post["id"], {"ready"}, "published", message=999)
        await self.store.report(1, post["id"], 500, "Concern")
        with self.assertRaises(ValueError):
            await self.store.report(1, post["id"], 500, "Again")
        await self.store.panel(1, 10, message=100)
        await self.store.panel(1, 10, count=True)
        self.assertEqual((await self.store.panel(1, 10))[1], 1)
        _, count, retired = await self.store.panel(1, 10, message=200, clear=True)
        self.assertEqual(count, 0)
        self.assertIn(100, retired)
        await self.store.retired_done(1, 10, 100)
        self.assertFalse((await self.store.panel(1, 10))[2])

    async def test_admin_permissions_are_required_even_for_moderators(self):
        permissions = discord.Permissions(moderate_members=True, ban_members=True)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7),
            guild=SimpleNamespace(
                fetch_member=AsyncMock(return_value=SimpleNamespace(guild_permissions=permissions))
            ),
        )
        with self.assertRaises(ValueError):
            await administrator(interaction)
        permissions.administrator = True
        await administrator(interaction)

    async def test_enabling_logs_later_does_not_reveal_old_authors(self):
        post = await self.submit()
        await self.configure(logging=True, log_channel=30)
        cog = Confessions(SimpleNamespace())
        cog.store = self.store
        cog.send_card = AsyncMock(return_value=SimpleNamespace(id=999))
        cog.refresh_queue = AsyncMock()
        with patch("ky_bot.confessions.command.destination", return_value=SimpleNamespace(id=10)):
            await cog.publish(SimpleNamespace(id=1), post, self.config)
        cog.send_card.assert_awaited_once()
        self.assertIsNone((await self.store.post(post["id"]))["author"])

    async def test_uncertain_public_delivery_is_never_automatically_repeated(self):
        post = await self.submit()
        cog = Confessions(SimpleNamespace())
        cog.store = self.store
        error = discord.HTTPException(
            SimpleNamespace(status=500, reason="Unavailable"), "Unconfirmed"
        )
        cog.send_card = AsyncMock(side_effect=error)
        with patch("ky_bot.confessions.command.destination", return_value=SimpleNamespace(id=10)):
            with self.assertRaises(discord.HTTPException):
                await cog.publish(SimpleNamespace(id=1), post, self.config)
            with self.assertRaises(ValueError):
                await cog.publish(SimpleNamespace(id=1), post, self.config)
        cog.send_card.assert_awaited_once()
        self.assertEqual((await self.store.post(post["id"]))["state"], "uncertain")

    async def test_log_failure_never_republishes_the_confession(self):
        await self.configure(logging=True, log_channel=30)
        post = await self.submit()
        cog = Confessions(SimpleNamespace())
        cog.store = self.store
        error = discord.HTTPException(
            SimpleNamespace(status=500, reason="Unavailable"), "Unconfirmed"
        )
        cog.send_card = AsyncMock(return_value=SimpleNamespace(id=999))
        cog.send_author_log = AsyncMock(side_effect=error)
        cog.refresh_queue = AsyncMock()
        with patch("ky_bot.confessions.command.destination", return_value=SimpleNamespace(id=10)):
            await cog.publish(SimpleNamespace(id=1), post, self.config)
            with self.assertRaises(ValueError):
                await cog.publish(SimpleNamespace(id=1), post, self.config)
        cog.send_card.assert_awaited_once()
        cog.send_author_log.assert_awaited_once()
        self.assertEqual((await self.store.post(post["id"]))["state"], "published")

    async def test_author_log_matches_compact_staff_embed_format(self):
        from ky_bot.confessions.cards import author_log_embed

        await self.configure(logging=True, log_channel=30, color="CAB6DF")
        post = await self.submit()
        post["data"]["message"] = 999
        embed = author_log_embed(self.config, post)
        data = embed.to_dict()
        fields = {field.name: field.value for field in embed.fields}
        self.assertIn("CONFESSION POSTED", embed.title)
        self.assertIn("𑁍", embed.title)
        self.assertIn("<@123>", fields["Author"])
        self.assertIn("User ID:", fields["Author"])
        self.assertEqual(fields["Channel"], "<#10>")
        self.assertEqual(fields["Confession text"], post["body"])
        self.assertEqual(embed.colour.value, 0xCAB6DF)
        self.assertNotIn("image", data)
        self.assertNotIn("thumbnail", data)
        self.assertEqual(embed.footer.text, "KY BOT · CONFESSIONS")
        self.assertIn("https://discord.com/channels/1/10/999", fields["Confession ID"])
        post["body"] = "A" * 2000
        long_log = author_log_embed(self.config, post)
        self.assertEqual(
            "".join(
                field.value
                for field in long_log.fields
                if field.name in {"Confession text", "Continued"}
            ),
            post["body"],
        )
        self.assertTrue(all(len(field.value) <= 1024 for field in long_log.fields))
        cog = Confessions(SimpleNamespace())
        channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=444)))
        await cog.send_author_log(channel, self.config, post)
        arguments = channel.send.await_args.kwargs
        self.assertIn("embed", arguments)
        self.assertNotIn("files", arguments)
        self.assertNotIn("view", arguments)

    async def test_shared_confession_settings_and_detached_sensitive_reviews(self):
        permissions = discord.Permissions(administrator=True)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7),
            guild_id=1,
            guild=SimpleNamespace(
                fetch_member=AsyncMock(return_value=SimpleNamespace(guild_permissions=permissions))
            ),
            message=SimpleNamespace(flags=SimpleNamespace(ephemeral=False)),
            response=SimpleNamespace(is_done=Mock(return_value=True), defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=500))),
            edit_original_response=AsyncMock(),
        )
        manager = SimpleNamespace(
            edit=AsyncMock(return_value=SimpleNamespace(id=100)), reset=AsyncMock()
        )
        shared = SimpleNamespace(
            panel_manager=manager,
            interaction_check=AsyncMock(return_value=True),
            section="overview",
            refresh=AsyncMock(),
        )
        detached = SimpleNamespace(panel_manager=None, section="overview", refresh=AsyncMock())
        cog = SimpleNamespace(store=self.store)
        menu = Admin(cog, 7, 1, parent=shared)
        menu.channel = 10
        for page in (
            "channels",
            "channel",
            "behavior",
            "logging",
            "appearance",
            "artwork",
            "lettering",
            "permissions",
            "bans",
        ):
            await menu.show(interaction, page)
            self.assertIs(manager.edit.await_args.args[2], menu)
            labels = {
                item.label for item in menu.walk_children() if isinstance(item, discord.ui.Button)
            }
            self.assertEqual("Home" in labels, page == "channels")
            self.assertEqual("Back" in labels, page != "channels")
        interaction.edit_original_response.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()
        await self.configure(approval=True)
        await self.submit()
        await menu.show(interaction, "logging")
        count = manager.edit.await_count
        for page in ("queue", "reports"):
            with patch("ky_bot.confessions.menus.private_parent", return_value=detached):
                await menu.show(interaction, page)
            private = interaction.followup.send.await_args.kwargs["view"]
            self.assertTrue(interaction.followup.send.await_args.kwargs["ephemeral"])
            self.assertIsNot(private, menu)
            self.assertIs(private.parent, detached)
            self.assertEqual(menu.page, "logging")
            self.assertEqual(private.page, page)
            self.assertEqual(manager.edit.await_count, count)
            self.assertNotIn("A private thought", str(menu.to_components()))
            interaction.message.flags.ephemeral = True
            reload = next(
                item
                for item in private.walk_children()
                if isinstance(item, discord.ui.Button) and item.label == "Reload list"
            )
            await reload.callback(interaction)
            self.assertEqual(private.page, page)
            self.assertEqual(manager.edit.await_count, count)
            buttons = {
                item.label: item
                for item in private.walk_children()
                if isinstance(item, discord.ui.Button)
            }
            self.assertNotIn("Back", buttons)
            self.assertNotIn("Home", buttons)
            await buttons["Close"].callback(interaction)
            result = interaction.edit_original_response.await_args.kwargs
            self.assertIsNone(result["content"])
            self.assertIsInstance(result["view"], discord.ui.LayoutView)
            self.assertEqual(result["view"].children[0].content, "Review closed.")
            self.assertEqual(result["attachments"], [])
            detached.refresh.assert_not_awaited()
            shared.refresh.assert_not_awaited()
            manager.reset.assert_not_awaited()
            self.assertEqual(manager.edit.await_count, count)
            private.stop()
            interaction.message.flags.ephemeral = False
        await menu.show(interaction, "channels")
        home = next(
            item
            for item in menu.walk_children()
            if isinstance(item, discord.ui.Button) and item.label == "Home"
        )
        await home.callback(interaction)
        manager.reset.assert_awaited_once_with(1)
        shared.refresh.assert_not_awaited()
        menu.stop()

    async def test_every_admin_page_serializes_within_discord_component_limit(self):
        permissions = discord.Permissions(administrator=True)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7),
            guild_id=1,
            guild=SimpleNamespace(
                fetch_member=AsyncMock(return_value=SimpleNamespace(guild_permissions=permissions))
            ),
            edit_original_response=AsyncMock(),
        )
        cog = SimpleNamespace(store=self.store)
        menu = Admin(cog, 7, 1)
        menu.channel = 10
        for page in (
            "channels",
            "channel",
            "behavior",
            "logging",
            "appearance",
            "artwork",
            "lettering",
            "permissions",
            "queue",
            "reports",
            "bans",
        ):
            await menu.show(interaction, page)
            self.assertLessEqual(menu.total_children_count, 40, page)
            menu.to_components()
            panel = menu.children[0]
            intros = [
                index
                for index, child in enumerate(panel.children)
                if isinstance(child, (discord.ui.TextDisplay, discord.ui.Section))
                and (
                    child.content
                    if isinstance(child, discord.ui.TextDisplay)
                    else child.children[0].content
                ).startswith(
                    (
                        "Configure ",
                        "Anonymous member submissions.",
                        "Administrators only",
                        "Review member reports",
                    )
                )
            ]
            self.assertEqual(len(intros), 1, page)
            self.assertIsInstance(panel.children[intros[0] + 1], discord.ui.MediaGallery, page)
            self.assertEqual(
                sum(isinstance(child, discord.ui.Separator) for child in panel.children), 1, page
            )
            labels = [
                item.label for item in menu.walk_children() if isinstance(item, discord.ui.Button)
            ]
            self.assertNotIn("Refresh", labels, page)
            self.assertEqual("Reload list" in labels, page in {"queue", "reports"}, page)
            self.assertNotIn("Home", labels, page)
            self.assertTrue(menu.header_art, page)
            self.assertFalse(
                any(
                    isinstance(child, discord.ui.MediaGallery)
                    and any("confession_title.png" in item.media.url for item in child.items)
                    for child in panel.children
                ),
                page,
            )
            strips = [
                item.media.url.removeprefix("attachment://")
                for child in panel.children
                if isinstance(child, discord.ui.MediaGallery)
                for item in child.items
                if "attachment://ky_strip_" in item.media.url
            ]
            self.assertTrue(strips, page)
            sent = interaction.edit_original_response.call_args.kwargs["attachments"]
            names = {file.filename for file in sent}
            self.assertNotIn("confession_title.png", names, page)
            self.assertTrue(set(strips) <= names, page)
            for left, middle, right in zip(panel.children, panel.children[1:], panel.children[2:]):
                self.assertFalse(
                    isinstance(left, discord.ui.Section)
                    and isinstance(middle, discord.ui.Separator)
                    and isinstance(right, discord.ui.Section)
                    and (right.accessory.label or "").lower() not in {"home", "back", "close"},
                    page,
                )
        menu.stop()

    async def test_logs_hub_uses_existing_feature_settings_and_fits(self):
        from ky_bot.confessions.checklist_logs import ChecklistLogs
        from ky_bot.confessions.logs import LogMenu
        from ky_bot.confessions.menus import BanMember

        permissions = discord.Permissions(administrator=True)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7),
            guild_id=1,
            guild=SimpleNamespace(
                fetch_member=AsyncMock(return_value=SimpleNamespace(guild_permissions=permissions))
            ),
            edit_original_response=AsyncMock(),
        )
        feature = SimpleNamespace(open_menu=AsyncMock())
        parent = SimpleNamespace()
        cog = SimpleNamespace(bot=SimpleNamespace(get_cog=lambda name: feature))
        menu = LogMenu(cog, 7, 1, parent)
        await menu.show(interaction)
        self.assertLessEqual(menu.total_children_count, 40)
        self.assertIn("OTHER LOGS", str(menu.to_components()))
        section = next(
            child
            for child in menu.children[0].children
            if isinstance(child, discord.ui.Section)
            and child.children[0].content.startswith("**CONFESSIONS**")
        )
        await section.accessory.callback(interaction)
        feature.open_menu.assert_awaited_once_with(interaction, command_center=parent, logs=True)
        await menu.show(interaction, True)
        self.assertLessEqual(menu.total_children_count, 40)
        menu.stop()
        modal = BanMember(SimpleNamespace())
        self.assertIsInstance(modal.member, discord.ui.UserSelect)
        modal.to_dict()
        picker = ChecklistLogs(SimpleNamespace(), 7, 1)
        picker.to_components()
        picker.stop()

    async def test_channel_appearance_does_not_restyle_its_admin_menu(self):
        from ky_bot.confessions.cards import uploads

        permissions = discord.Permissions(administrator=True)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=7),
            guild_id=1,
            guild=SimpleNamespace(
                fetch_member=AsyncMock(return_value=SimpleNamespace(guild_permissions=permissions))
            ),
            edit_original_response=AsyncMock(),
        )
        menu = Admin(SimpleNamespace(store=self.store), 7, 1)
        menu.channel = 10
        await menu.show(interaction, "artwork")
        original_header = menu.header_art
        original_accent = menu.children[0].accent_colour
        await self.configure(
            artwork_finish="red_iridescent",
            text_finish="gold",
            text_color="#FF3366",
            color="FF3366",
        )
        with (
            patch("ky_bot.confessions.menus.uploads", wraps=uploads) as menu_uploads,
            patch("ky_bot.confessions.menus.attachments", return_value=[]) as strips,
        ):
            await menu.show(interaction, "artwork")
            self.assertEqual(menu.header_art, original_header)
            self.assertEqual(menu.children[0].accent_colour, original_accent)
            self.assertEqual(menu_uploads.call_args.args[0]["artwork_finish"], "silver_neon")
            self.assertEqual(strips.call_args.args[1]["text_finish"], "silver_neon")
            self.assertIsNone(strips.call_args.args[1]["text_color"])
        self.assertEqual(menu.config["artwork_finish"], "red_iridescent")
        public_files = uploads(menu.config)
        standard_files = uploads(defaults())
        try:
            self.assertNotEqual(public_files[0].fp.getvalue(), standard_files[0].fp.getvalue())
            self.assertNotEqual(public_files[1].fp.getvalue(), standard_files[1].fp.getvalue())
        finally:
            for file in public_files + standard_files:
                file.close()
        menu.stop()

    async def test_channel_appearance_never_changes_another_channel(self):
        from ky_bot.confessions.cards import uploads
        from ky_bot.confessions.headings import title_image

        await self.store.configure(
            1,
            11,
            None,
            enabled=True,
            header_title="AFTER HOURS",
            artwork_finish="gold",
            text_finish="gold",
            color="AA9933",
        )
        await self.configure(
            header_title="SECRETS",
            artwork_finish="red_iridescent",
            text_finish="silver_neon",
            text_color="#39dccb",
            color="901133",
        )
        _, other = await self.store.config(1, 11)
        self.assertEqual(other["header_title"], "AFTER HOURS")
        self.assertEqual(other["artwork_finish"], "gold")
        self.assertEqual(other["text_finish"], "gold")
        self.assertIsNone(other["text_color"])
        self.assertEqual(self.config["text_color"], "#39DCCB")
        first, second = uploads(self.config), uploads(other)
        try:
            self.assertNotEqual(first[0].fp.read(), second[0].fp.read())
            self.assertNotEqual(first[1].fp.read(), second[1].fp.read())
        finally:
            for file in first + second:
                file.close()
        self.assertNotEqual(
            title_image("SUBMIT CONFESSION", "gold"),
            title_image("SUBMIT CONFESSION", "silver_neon", "#39DCCB"),
        )
        for changes in (
            {"text_color": "invalid"},
            {"text_finish": "unknown"},
            {"artwork_finish": "unknown"},
            {"header_title": ""},
        ):
            with self.assertRaises(ValueError):
                validate(defaults() | changes)

    def test_invalid_settings(self):
        for changes in (
            {"color": "invalid"},
            {"sticky_count": 0},
            {"cooldown": -1},
            {"approval": True},
            {"logging": True},
            {"max_length": 2001},
        ):
            with self.assertRaises(ValueError):
                validate(defaults() | changes)
        self.assertEqual(Confessions.confess.name, "confess")


if __name__ == "__main__":
    unittest.main()
