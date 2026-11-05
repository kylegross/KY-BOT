import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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
        cog.send_card = AsyncMock(side_effect=[SimpleNamespace(id=999), error])
        cog.refresh_queue = AsyncMock()
        with patch("ky_bot.confessions.command.destination", return_value=SimpleNamespace(id=10)):
            await cog.publish(SimpleNamespace(id=1), post, self.config)
            with self.assertRaises(ValueError):
                await cog.publish(SimpleNamespace(id=1), post, self.config)
        self.assertEqual(cog.send_card.await_count, 2)
        self.assertEqual((await self.store.post(post["id"]))["state"], "published")

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
            "permissions",
            "queue",
            "reports",
            "bans",
        ):
            await menu.show(interaction, page)
            self.assertLessEqual(menu.total_children_count, 40, page)
            menu.to_components()
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
