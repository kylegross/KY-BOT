import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import discord

from ky_bot.admin_functions.activity import Activity
from ky_bot.admin_functions.autodelete.autodelete_logging import TYPES, ActivityLogs
from ky_bot.admin_functions.autodelete.autodelete_store import RuleStore
from ky_bot.admin_functions.settings_repository import GuildSettings, open_settings
from ky_bot.nsfw.command import Menu, NSFWFlashLogs
from ky_bot.nsfw.spoilers import violates


class FlashTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "logs.sqlite3"
        self.store = RuleStore(self.path)
        self.guild = NS(id=1, me=NS(id=99))
        permissions = discord.Permissions.all()
        self.source = NS(
            id=10,
            name="flash",
            guild=self.guild,
            is_nsfw=lambda: True,
            permissions_for=lambda _: permissions,
            edit=AsyncMock(),
            send=AsyncMock(),
        )
        self.destination = NS(
            id=20,
            name="staff-flash",
            guild=self.guild,
            is_nsfw=lambda: True,
            permissions_for=lambda _: permissions,
            send=AsyncMock(),
        )
        self.channels = {10: self.source, 20: self.destination}
        self.guild.get_channel = lambda ident: self.channels.get(ident)
        self.guild.get_channel_or_thread = self.guild.get_channel
        self.guild.get_member = lambda ident: None
        self.guild.get_role = lambda ident: None
        self.guild.fetch_member = AsyncMock(return_value=NS(guild_permissions=permissions))
        self.bot = NS(
            get_guild=lambda _: self.guild,
            get_channel=lambda ident: self.channels.get(ident),
            fetch_user=AsyncMock(),
            user=NS(id=99),
        )
        self.logs = ActivityLogs(self.bot, self.store)
        self.bot.get_cog = lambda name: (
            NS(service=NS(logs=self.logs)) if name == "AutoDelete" else None
        )
        self.logs.save_flash(1, 10, 20, list(TYPES), True)
        self.cog = NSFWFlashLogs(self.bot)
        self.cog.channel = AsyncMock(return_value=self.source)
        self.cog.destination = AsyncMock(return_value=self.destination)
        self.i = NS(user=NS(id=5), guild=self.guild, guild_id=1)

    async def asyncTearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def message(self, ident=100, content="Caption", attachments=None):
        return NS(
            id=ident,
            guild=self.guild,
            channel=self.source,
            content=content,
            author=NS(id=5, display_name="Athena✨", bot=False, send=AsyncMock()),
            webhook_id=None,
            attachments=attachments or [],
            embeds=[],
            mentions=[],
            role_mentions=[],
            stickers=[],
            reference=None,
            created_at=discord.utils.utcnow(),
            delete=AsyncMock(),
        )

    def upload(self, *, spoiler=False, kind="image/png"):
        return NS(
            content_type=kind,
            filename="private.gif" if kind == "image/gif" else "private.png",
            url="https://cdn.discordapp.com/attachments/private-media",
            proxy_url="https://media.discordapp.net/private-media",
            is_spoiler=lambda: spoiler,
        )

    def events(self):
        return [dict(row) for row in self.store.db.execute("SELECT * FROM activity_log_events")]

    async def test_flash_logging_does_not_need_an_autodelete_rule(self):
        self.assertEqual(self.store.list(1), [])
        await self.logs.on_message(self.message())
        self.assertEqual(len(self.events()), 1)
        self.assertIn("MESSAGE SENT", self.events()[0]["body"])

    async def test_media_privacy_and_gif_attachment_urls_are_not_retained(self):
        for n, kind in enumerate(("image/png", "video/mp4", "image/gif")):
            m = self.message(
                100 + n, "Caption https://example.com/adult.jpg", [self.upload(kind=kind)]
            )
            await self.logs.on_message(m)
        rows = self.store.db.execute("SELECT data FROM activity_log_messages").fetchall()
        saved = " ".join(row["data"] for row in rows) + " ".join(x["body"] for x in self.events())
        self.assertNotIn("private-media", saved)
        self.assertNotIn("private.png", saved)
        self.assertNotIn("private.gif", saved)
        self.assertNotIn("adult.jpg", saved)
        self.assertIn("Caption", saved)

    async def test_bot_cleanup_does_not_add_deletion_events(self):
        m = self.message()
        await self.logs.on_message(m)
        self.logs.begin_deletion(m)
        self.logs.finish_deletion(m, True)
        await self.logs.on_delete(NS(guild_id=1, channel_id=10, message_id=m.id, cached_message=m))
        self.assertEqual(len(self.events()), 1)
        self.assertNotIn("MESSAGE DELETED", self.events()[0]["body"])

    async def test_manual_delete_is_recorded_but_actor_not_invented(self):
        m = self.message()
        await self.logs.on_message(m)
        await self.logs.on_delete(NS(guild_id=1, channel_id=10, message_id=m.id, cached_message=m))
        self.assertEqual(len(self.events()), 2)
        self.assertIn("not identified by Discord", self.events()[1]["body"])

    async def test_grouping_keeps_same_member_events_in_one_record(self):
        await self.logs.on_message(self.message(100, "First caption"))
        await self.logs.on_message(self.message(101, "Second caption"))
        pages = self.store.db.execute("SELECT * FROM activity_log_queue").fetchall()
        self.assertEqual(len(pages), 1)
        self.assertIn("First caption", pages[0]["payload"])
        self.assertIn("Second caption", pages[0]["payload"])

    async def test_compaction_keeps_receipts_and_never_discards_pending_payloads(self):
        await self.logs.on_message(self.message())
        self.assertEqual(self.logs.compact_delivered(now=time.time() + 121), 0)
        with self.store.db:
            self.store.db.execute("UPDATE activity_log_queue SET state='sent',message=777")
        self.assertEqual(self.logs.compact_delivered(now=time.time() + 121), 1)
        row = self.store.db.execute("SELECT * FROM activity_log_queue").fetchone()
        self.assertEqual(row["payload"], "")
        self.assertEqual(row["message"], 777)
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.events()[0]["body"], "")

    async def test_old_retention_jobs_do_not_delete_discord_logs(self):
        self.store.db.execute("INSERT INTO activity_log_retention VALUES (777,1,20,0,0)")
        self.store.db.commit()
        await self.logs.expire_logs()
        self.assertEqual(
            self.store.db.execute("SELECT COUNT(*) FROM activity_log_retention").fetchone()[0], 0
        )
        self.destination.send.assert_not_awaited()

    async def test_spoiler_policy_preserves_protected_posts_and_suppresses_its_removals(self):
        self.logs.set_spoilers(1, 10, True)
        m = self.message(attachments=[self.upload(spoiler=True)])
        await self.logs.on_message(m)
        m.delete.assert_not_awaited()
        bad = self.message(101, attachments=[self.upload()])
        await self.logs.on_message(bad)
        bad.delete.assert_awaited_once()
        bad.author.send.assert_awaited_once()
        self.assertTrue(Activity(self.bot).auto_deleted(1, 101))
        await self.logs.on_delete(NS(guild_id=1, channel_id=10, message_id=101, cached_message=bad))
        self.assertEqual(len(self.events()), 1)

    async def test_spoiler_setting_off_allows_unspoilered_uploads(self):
        m = self.message(attachments=[self.upload()])
        await self.logs.on_message(m)
        m.delete.assert_not_awaited()
        self.assertEqual(len(self.events()), 1)

    async def test_media_links_require_every_occurrence_to_be_hidden(self):
        for text, expected in (
            ("https://example.com/a.mp4", True),
            ("||https://example.com/a.mp4||", False),
            ("||https://example.com/a.mp4|| https://example.com/a.mp4", True),
        ):
            self.assertEqual(violates(self.message(content=text)), expected)

    async def test_slash_and_menu_settings_preserve_other_channel_configuration(self):
        await self.cog.apply(self.i, 10, adult=True, require_spoilers=True)
        self.assertTrue(self.logs.spoiler_required(1, 10))
        self.assertIsNone(self.logs.flash_config(1, 11))
        await self.cog.apply(self.i, 10, adult=False)
        self.assertFalse(self.logs.flash_config(1, 10)["enabled"])
        self.source.edit.assert_awaited_once()
        view = Menu(self.cog, 5, 1, self.source)
        self.assertLessEqual(view.total_children_count, 40)
        self.assertLessEqual(view.content_length(), 4000)
        self.assertIn("IMAGES AND VIDEOS ARE NOT SAVED", str(view.to_components()))
        view.stop()

    async def test_fresh_admin_permission_is_required(self):
        self.guild.fetch_member.return_value = NS(guild_permissions=discord.Permissions.none())
        with self.assertRaises(ValueError):
            await self.cog.apply(self.i, 10, adult=False)
        self.source.edit.assert_not_awaited()

    async def test_server_role_changes_are_grouped_by_member(self):
        self.bot.server_settings = NS(
            repository=NS(get=AsyncMock(return_value=GuildSettings(1, log_channel_id=20))),
            validate_channel=AsyncMock(return_value=self.destination),
        )
        activity = Activity(self.bot)
        first = NS(id=5, display_name="Athena", roles=[], guild=self.guild)
        second = NS(
            id=5, display_name="Athena", roles=[NS(id=10, name="Role One")], guild=self.guild
        )
        third = NS(
            id=5,
            display_name="Athena",
            roles=second.roles + [NS(id=11, name="Role Two")],
            guild=self.guild,
        )
        await activity.on_member_update(first, second)
        await activity.on_member_update(second, third)
        rows = self.store.db.execute("SELECT payload FROM activity_log_queue").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertIn("Role One", rows[0]["payload"])
        self.assertIn("Role Two", rows[0]["payload"])
        self.assertNotIn("<#0>", json.loads(rows[0]["payload"])["text"])

    async def test_excluded_category_suppresses_thread_server_events(self):
        self.bot.server_settings = NS(
            repository=NS(
                get=AsyncMock(
                    return_value=GuildSettings(1, log_channel_id=20, log_excluded_channel_ids=(30,))
                )
            ),
            validate_channel=AsyncMock(),
        )
        self.channels[12] = NS(id=12, parent_id=10, parent=NS(category_id=30))
        await Activity(self.bot).emit(
            1, "MESSAGE EDITED", {"Message ID": "100"}, source_channel_id=12
        )
        self.bot.server_settings.validate_channel.assert_not_awaited()
        self.assertEqual(self.events(), [])

    async def test_exclusions_persist_and_are_server_scoped(self):
        async with open_settings(Path(self.temp.name) / "settings.sqlite3") as repo:
            await repo.set_log_exclusions(1, [10, 30, 10])
            self.assertEqual((await repo.get(1)).log_excluded_channel_ids, (10, 30))
            self.assertEqual((await repo.get(2)).log_excluded_channel_ids, ())
            await repo.set_log_exclusions(1, [])
            self.assertEqual((await repo.get(1)).log_excluded_channel_ids, ())

    async def test_delivery_updates_original_summary_instead_of_posting_again(self):
        posted = NS(id=777, author=NS(id=99), edit=AsyncMock())
        self.destination.send.return_value = posted
        self.destination.fetch_message = AsyncMock(return_value=posted)
        await self.logs.on_message(self.message(100, "First update"))
        await self.logs.flush()
        await self.logs.on_message(self.message(101, "Second update"))
        await self.logs.flush()
        self.destination.send.assert_awaited_once()
        posted.edit.assert_awaited_once()
        description = posted.edit.await_args.kwargs["embed"].description
        self.assertIn("First update", description)
        self.assertIn("Second update", description)

    async def test_partial_delivery_does_not_compact_retry_pages(self):
        await self.logs.on_message(self.message(content="x" * 4000))
        rows = self.store.db.execute("SELECT * FROM activity_log_queue ORDER BY id").fetchall()
        self.assertGreater(len(rows), 1)
        with self.store.db:
            self.store.db.execute(
                "UPDATE activity_log_queue SET state='sent',message=777 WHERE id=?",
                (rows[0]["id"],),
            )
        self.assertEqual(self.logs.compact_delivered(now=time.time() + 121), 0)
        pending = self.store.db.execute(
            "SELECT payload FROM activity_log_queue WHERE state='pending'"
        ).fetchone()
        self.assertTrue(pending["payload"])

    async def test_spoilers_work_without_flash_logging_enabled(self):
        self.logs.save_flash(1, 10, 20, list(TYPES), False)
        self.logs.set_spoilers(1, 10, True)
        message = self.message(attachments=[self.upload()])
        await self.logs.on_message(message)
        message.delete.assert_awaited_once()
        self.assertEqual(self.events(), [])

    async def test_confirmed_cleanup_receipt_survives_later_failed_attempt(self):
        message = self.message()
        self.logs.begin_deletion(message)
        self.logs.finish_deletion(message, True)
        self.logs.begin_deletion(message)
        self.logs.finish_deletion(message, False)
        self.assertTrue(Activity(self.bot).auto_deleted(1, message.id))

    async def test_postgresql_adapter_namespaces_new_configuration_tables(self):
        from ky_bot.admin_functions.autodelete.postgres import Connection

        database = Mock()
        connection = Connection(database)
        connection.execute("INSERT OR REPLACE INTO nsfw_spoiler_config VALUES (?,?,?)", (1, 10, 1))
        statement = database.execute.call_args.args[0]
        self.assertIn("autodelete_nsfw_spoiler_config", statement)
        self.assertIn("ON CONFLICT(guild,source)", statement)
        self.assertIn("required=excluded.required", statement)
        connection.execute(
            "SELECT body FROM activity_log_events WHERE batch=? ORDER BY event_order,token",
            ("batch",),
        )
        statement = database.execute.call_args.args[0]
        self.assertNotIn("rowid", statement)
        self.assertIn("autodelete_activity_log_events", statement)

    async def test_unrestricted_log_destination_is_rejected(self):
        self.cog.channel = AsyncMock(return_value=self.destination)
        self.destination.is_nsfw = lambda: False
        with self.assertRaises(ValueError):
            await NSFWFlashLogs.destination(self.cog, 1, 20, 10)
