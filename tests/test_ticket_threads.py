import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from ky_bot.tickets.command import Tickets
from ky_bot.tickets.store import Store, defaults, new_panel, new_type
from ky_bot.tickets.threads import sync_access


class TicketThreadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = await Store.open(":memory:")
        config = defaults()
        config["enabled"] = True
        config["defaults"].update(support_roles=[7], manager_roles=[8], cooldown=0)
        config["panels"]["a" * 32] = new_panel("Support", 200) | dict(
            types=["b" * 32], published_channel=200
        )
        config["types"]["b" * 32] = new_type("Support")
        await self.store.configure(1, None, config)
        self.owner = SimpleNamespace(id=5, bot=False, roles=[], send=AsyncMock())
        self.staff = SimpleNamespace(id=9, bot=False, roles=[SimpleNamespace(id=7)])
        self.guild = SimpleNamespace(
            id=1,
            chunked=True,
            members=[self.owner, self.staff],
            me=SimpleNamespace(id=100),
            get_member=lambda key: self.owner if key == 5 else self.staff,
        )
        self.thread = Mock(spec=discord.Thread)
        self.thread.id = 300
        self.thread.guild = self.guild
        self.thread.parent_id = 200
        self.thread.parent = Mock(spec=discord.TextChannel)
        self.thread.parent.permissions_for = Mock(return_value=discord.Permissions.all())
        self.thread.fetch_members = AsyncMock(
            return_value=[SimpleNamespace(id=100), SimpleNamespace(id=5)]
        )
        self.thread.add_user = AsyncMock()
        self.thread.remove_user = AsyncMock()
        self.thread.edit = AsyncMock()
        self.thread.send = AsyncMock()
        self.thread.mention = "<#300>"
        self.cog = Tickets(SimpleNamespace(get_guild=lambda key: self.guild))
        self.cog.store = self.store
        self.cog.get_channel = AsyncMock(return_value=self.thread)
        self.cog.transcript = AsyncMock()
        self.cog.refresh_intro = AsyncMock()
        ticket = await self.store.reserve(
            1, 5, "a" * 32, "b" * 32, [], owner_username="example.user"
        )
        self.ticket = await self.store.update(
            ticket, channel=300, state="waiting_staff", intro_sent=True
        )

    async def asyncTearDown(self):
        await self.store.close()

    async def test_private_thread_membership_approval_and_closure(self):
        await sync_access(self.thread, self.ticket, pending=True)
        self.assertIn(5, {call.args[0].id for call in self.thread.remove_user.await_args_list})
        self.assertEqual({call.args[0].id for call in self.thread.add_user.await_args_list}, {9})
        self.thread.fetch_members.return_value = [SimpleNamespace(id=100), SimpleNamespace(id=9)]
        self.thread.add_user.reset_mock()
        await sync_access(self.thread, self.ticket)
        self.assertEqual(self.thread.add_user.await_args.args[0].id, 5)

    async def test_close_sets_persistent_24_hour_deadline_and_archives_once(self):
        with patch("ky_bot.tickets.command.time.time", return_value=100000):
            await self.cog.close_ticket(self.ticket, 9, "Resolved")
        ticket = await self.store.get(self.ticket["id"])
        self.assertEqual(ticket["archive_at"], 186400)
        self.assertEqual(ticket["state"], "closed")
        self.assertFalse(self.thread.edit.await_args.kwargs["archived"])
        self.assertTrue(self.thread.edit.await_args.kwargs["locked"])
        self.thread.edit.reset_mock()
        with patch("ky_bot.tickets.command.time.time", return_value=186399):
            await self.cog.maintain_ticket(ticket)
        self.thread.edit.assert_not_awaited()
        await self.store.update(ticket, paused=True, last_activity=186399)
        # New cog models a restart; timing comes from closure, not later activity.
        restarted = Tickets(SimpleNamespace(get_guild=lambda key: self.guild))
        restarted.store = self.store
        restarted.get_channel = AsyncMock(return_value=self.thread)
        with patch("ky_bot.tickets.command.time.time", return_value=186400):
            await restarted.maintain_ticket(await self.store.get(ticket["id"]))
            await restarted.maintain_ticket(await self.store.get(ticket["id"]))
        self.thread.edit.assert_awaited_once_with(
            archived=True, locked=True, reason="Ticket closed for 24 hours"
        )
        self.assertEqual((await self.store.get(ticket["id"]))["thread_archived_at"], 186400)

    async def test_reopen_unarchives_and_clears_deadline(self):
        ticket = await self.store.update(
            self.ticket, state="closed", closed_at=100, archive_at=86500, thread_archived_at=86500
        )
        user = Mock(spec=discord.Member)
        user.id = 9
        user.roles = [SimpleNamespace(id=7)]
        user.guild_permissions = SimpleNamespace(administrator=False)
        interaction = SimpleNamespace(
            guild_id=1,
            guild=self.guild,
            user=user,
            response=SimpleNamespace(defer=AsyncMock(), is_done=Mock(return_value=True)),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await self.cog.action(interaction, ticket["id"], "reopen")
        self.thread.edit.assert_awaited_with(
            archived=False, locked=False, auto_archive_duration=10080
        )
        updated = await self.store.get(ticket["id"])
        self.assertEqual(updated["archive_at"], 0)
        self.assertEqual(updated["thread_archived_at"], 0)
        self.assertEqual(updated["state"], "waiting_staff")

    async def test_approval_invites_owner_and_sends_thread_link(self):
        settings = self.ticket["settings"] | dict(require_approval=True)
        ticket = await self.store.update(self.ticket, state="pending_approval", settings=settings)
        self.thread.fetch_members.return_value = [SimpleNamespace(id=100), SimpleNamespace(id=9)]
        user = Mock(spec=discord.Member)
        user.id = 9
        user.roles = [SimpleNamespace(id=8)]
        user.guild_permissions = SimpleNamespace(administrator=False)
        interaction = SimpleNamespace(
            guild_id=1,
            guild=self.guild,
            user=user,
            response=SimpleNamespace(defer=AsyncMock(), is_done=Mock(return_value=True)),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await self.cog.action(interaction, ticket["id"], "approve")
        self.assertEqual(self.thread.add_user.await_args.args[0].id, 5)
        self.assertIn("<#300>", self.owner.send.await_args.args[0])
        self.assertEqual((await self.store.get(ticket["id"]))["state"], "waiting_staff")

    async def test_creating_thread_recovers_without_creating_another_channel(self):
        ticket = await self.store.update(self.ticket, state="creating", channel=0)
        self.thread.name = "ky-ticket-" + ticket["id"]
        self.thread.owner_id = 100
        self.thread.type = discord.ChannelType.private_thread
        self.guild.active_threads = AsyncMock(return_value=[self.thread])
        self.cog.send_intro = AsyncMock()
        with patch("ky_bot.tickets.command.time.time", return_value=ticket["created"] + 121):
            await self.cog.maintain_ticket(ticket)
        updated = await self.store.get(ticket["id"])
        self.assertEqual(updated["channel"], 300)
        self.assertEqual(updated["state"], "waiting_staff")
        self.assertEqual(self.thread.edit.await_args.kwargs["name"], "example-user-1")
        self.cog.send_intro.assert_awaited_once()

    async def test_missing_thread_permissions_are_rejected(self):
        from ky_bot.tickets.threads import validate_parent

        parent = Mock(spec=discord.TextChannel)
        parent.guild = self.guild
        permissions = discord.Permissions.all()
        permissions.manage_threads = False
        parent.permissions_for = Mock(return_value=permissions)
        with self.assertRaisesRegex(ValueError, "manage threads"):
            validate_parent(parent)
