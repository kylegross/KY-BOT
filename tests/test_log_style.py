import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from ky_bot.log_style import ICON, PREFIX, SUFFIX, send_log, style_log


class LogStyleTests(unittest.IsolatedAsyncioTestCase):
    async def test_shared_style_preserves_fields_status_colour_and_timestamp(self):
        for title in (
            "MEMBER JOINED",
            "MESSAGE DELETED",
            "NSFW FLASH LOG",
            "ASK TO DM/FR · ACCEPTED",
            "CONFESSION POSTED",
            "CHECKLIST TASK COMPLETED",
        ):
            stamp = datetime(2026, 11, 5, tzinfo=UTC)
            embed = discord.Embed(
                title=title,
                description="Appropriate event details",
                colour=0x57F287,
                timestamp=stamp,
            )
            embed.add_field(name="Member", value="User ID: `123`", inline=False)
            style_log(embed)
            once = embed.to_dict()
            style_log(embed)
            self.assertEqual(embed.to_dict(), once)
            self.assertEqual(embed.title, PREFIX + title + SUFFIX)
            self.assertEqual(embed.footer.text, "KY BOT")
            self.assertEqual(embed.footer.icon_url, ICON)
            self.assertEqual(embed.timestamp, stamp)
            self.assertEqual(embed.fields[0].value, "User ID: `123`")
            self.assertEqual(embed.colour.value, 0x57F287)

    async def test_send_and_edit_attach_only_branding_and_never_ping(self):
        messages = []

        async def capture(**kwargs):
            file = kwargs.get("file") or kwargs["attachments"][0]
            self.assertEqual(file.filename, "ky_bot_logo.png")
            self.assertTrue(file.fp.read().startswith(b"\x89PNG"))
            self.assertFalse(kwargs["allowed_mentions"].everyone)
            messages.append(kwargs["embed"].to_dict())
            return SimpleNamespace(id=42)

        target = SimpleNamespace(
            send=AsyncMock(side_effect=capture), edit=AsyncMock(side_effect=capture)
        )
        await send_log(target, discord.Embed(title="PENDING"))
        await send_log(target, discord.Embed(title="ACCEPTED"), edit=True)
        target.send.assert_awaited_once()
        target.edit.assert_awaited_once()
        self.assertIn("ACCEPTED", messages[-1]["title"])

    async def test_role_changes_merge_into_one_readable_member_summary(self):
        import json

        from ky_bot.admin_functions.autodelete.autodelete_logging import ActivityLogs
        from ky_bot.admin_functions.autodelete.autodelete_store import RuleStore

        store = RuleStore(":memory:")
        try:
            logger = ActivityLogs(SimpleNamespace(), store)
            config = dict(
                guild=1,
                source=0,
                destination=20,
                title="KY BOT · SERVER ACTIVITY",
                color=0xC9DDF0,
                types="[]",
                mode="server",
                actor_label="Member ID",
            )
            for n, role in enumerate((10, 20, 10)):
                logger.queue(
                    config,
                    0,
                    123,
                    "MEMBER ROLES CHANGED",
                    dict(
                        name="Member",
                        user=123,
                        role_changes=dict(added=[dict(id=role, name="Role")], removed=[]),
                    ),
                    "Old verbose details",
                    token=str(n),
                )
            rows = store.db.execute("SELECT payload FROM activity_log_queue").fetchall()
            self.assertEqual(len(rows), 1)
            payload = json.loads(rows[0]["payload"])
            self.assertEqual(payload["title"], "MEMBER ROLES CHANGED")
            self.assertEqual(payload["text"].count("User ID:"), 1)
            self.assertEqual(payload["text"].count("<@&10>"), 1)
            self.assertIn("<@&20>", payload["text"])
            self.assertIn("**Roles added**", payload["text"])
            self.assertNotIn("One-minute", payload["text"])
            self.assertNotIn("Old verbose", payload["text"])
            logger.queue(
                config,
                0,
                123,
                "MEMBER ROLES CHANGED",
                dict(
                    name="Member",
                    user=123,
                    role_changes=dict(added=[], removed=[dict(id=10, name="Role")]),
                ),
                "",
                token="remove",
            )
            payload = json.loads(
                store.db.execute("SELECT payload FROM activity_log_queue").fetchone()[0]
            )
            self.assertIn("**Roles removed**\n<@&10>", payload["text"])
            self.assertIn("**Roles added**", payload["text"])
            logger.queue(
                config,
                0,
                456,
                "MEMBER ROLES CHANGED",
                dict(
                    name="Other",
                    user=456,
                    role_changes=dict(added=[dict(id=30, name="Other")], removed=[]),
                ),
                "",
                token="other",
            )
            self.assertEqual(
                store.db.execute("SELECT COUNT(*) FROM activity_log_queue").fetchone()[0], 2
            )
        finally:
            store.db.close()

    async def test_many_role_changes_keep_complete_mentions_on_continuation_pages(self):
        import json
        import re

        from ky_bot.admin_functions.autodelete.autodelete_logging import ActivityLogs
        from ky_bot.admin_functions.autodelete.autodelete_store import RuleStore

        store = RuleStore(":memory:")
        try:
            logger = ActivityLogs(SimpleNamespace(), store)
            config = dict(
                guild=1,
                source=0,
                destination=20,
                title="SERVER ACTIVITY",
                color=1,
                types="[]",
                mode="server",
            )
            roles = [dict(id=100000000000000000 + n, name="Role") for n in range(300)]
            logger.queue(
                config,
                0,
                123,
                "MEMBER ROLES CHANGED",
                dict(name="Member", user=123, role_changes=dict(added=roles, removed=[])),
                "",
            )
            pages = [
                json.loads(row[0])
                for row in store.db.execute("SELECT payload FROM activity_log_queue")
            ]
            self.assertGreater(len(pages), 1)
            mentions = []
            for page in pages:
                self.assertLessEqual(len(page["text"]), 3900)
                mentions.extend(re.findall(r"<@&(\d+)>", page["text"]))
            self.assertEqual(set(mentions), {str(role["id"]) for role in roles})
        finally:
            store.db.close()

    async def test_title_fits_discord_limit(self):
        embed = style_log(discord.Embed(title="X" * 256))
        self.assertLessEqual(len(embed.title), 256)
        self.assertTrue(embed.title.endswith(SUFFIX))
