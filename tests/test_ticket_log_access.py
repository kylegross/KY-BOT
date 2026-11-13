import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from ky_bot.tickets.command import Tickets, staff
from ky_bot.tickets.log_access import channel_viewers, privacy_overwrites, viewer_roles
from ky_bot.tickets.store import Store, defaults, new_panel, new_type
from ky_bot.tickets.views import Admin

PANEL = "a" * 32
KIND = "b" * 32


def role(key, *, admin=False, default=False):
    value = Mock(spec=discord.Role)
    value.id = key
    value.permissions = discord.Permissions(administrator=admin)
    value.is_default = Mock(return_value=default)
    return value


def member(key, roles=(), *, bot=False, admin=False):
    value = Mock(spec=discord.Member)
    value.id, value.bot, value.roles = key, bot, list(roles)
    value.guild_permissions = discord.Permissions(administrator=admin)
    return value


class LogAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = await Store.open(":memory:")
        self.config = defaults()
        self.config["defaults"].update(
            support_roles=[7], manager_roles=[8], log_channels=[10], transcript_channels=[12]
        )
        self.config["panels"][PANEL] = new_panel("Support") | dict(types=[KIND])
        self.config["types"][KIND] = new_type("Support")
        self.revision = await self.store.configure(1, None, self.config)
        self.settings = self.config["defaults"]
        self.everyone, self.support, self.manager, self.viewer = (
            role(1, default=True),
            role(7),
            role(8),
            role(9),
        )
        self.bot_member = member(100, bot=True)
        self.guild = SimpleNamespace(
            id=1,
            me=self.bot_member,
            default_role=self.everyone,
            roles=[self.everyone, self.support, self.manager, self.viewer],
        )
        mapping = {r.id: r for r in self.guild.roles}
        self.guild.get_role = Mock(side_effect=mapping.get)
        self.channel = Mock(spec=discord.TextChannel)
        self.channel.id, self.channel.guild = 10, self.guild
        self.channel.overwrites = {}
        self.visible = {8}
        self.channel.permissions_for = Mock(
            side_effect=lambda target: discord.Permissions(
                view_channel=target.id in self.visible or target.id == 100,
                send_messages=True,
                embed_links=True,
                attach_files=True,
                read_message_history=True,
                manage_roles=True,
            )
        )
        self.cog = Tickets(SimpleNamespace())
        self.cog.store = self.store

    async def asyncTearDown(self):
        await self.store.close()

    async def test_managers_only_pass_without_support_visibility(self):
        self.assertEqual(viewer_roles(self.settings), {8})
        await self.cog.validate_log(self.channel, self.settings)
        self.visible.add(7)
        with self.assertRaisesRegex(ValueError, "unselected role"):
            await self.cog.validate_log(self.channel, self.settings)

    async def test_independent_log_role_does_not_grant_ticket_access(self):
        self.config["defaults"]["log_viewer_roles"] = [9]
        await self.store.configure(1, self.revision, self.config)
        self.visible = {9}
        await self.cog.validate_log(self.channel, self.config["defaults"])
        self.assertFalse(staff(member(50, [self.viewer]), self.config["defaults"]))

    async def test_explicit_empty_roles_is_administrators_only(self):
        self.config["defaults"]["log_viewer_roles"] = []
        await self.store.configure(1, self.revision, self.config)
        self.visible = set()
        await self.cog.validate_log(self.channel, self.settings)
        self.visible = {8}
        with self.assertRaises(ValueError):
            await self.cog.validate_log(self.channel, self.settings)

    async def test_direct_member_overwrite_cannot_bypass_viewer_roles(self):
        outsider = member(50, [self.support])
        self.channel.overwrites = {outsider: discord.PermissionOverwrite(view_channel=True)}
        self.visible.add(50)
        with self.assertRaisesRegex(ValueError, "unselected member"):
            await self.cog.validate_log(self.channel, self.settings)

    async def test_shared_channels_union_only_their_routes(self):
        other = "c" * 32
        self.config["panels"][other] = new_panel("Other") | dict(
            types=[KIND],
            overrides=dict(log_channels=[20], transcript_channels=[22], log_viewer_roles=[9]),
        )
        self.assertEqual(channel_viewers(self.config, 10, self.settings), {8})
        self.config["panels"][other]["overrides"]["log_channels"] = [10]
        self.assertEqual(channel_viewers(self.config, 10, self.settings), {8, 9})

    async def test_permission_update_removes_support_and_member_bypasses(self):
        outsider = member(50, [self.support])
        allowed = member(51, [self.manager])
        original = {
            self.support: discord.PermissionOverwrite(view_channel=True, send_messages=False),
            outsider: discord.PermissionOverwrite(view_channel=True),
            allowed: discord.PermissionOverwrite(view_channel=False),
        }
        self.channel.overwrites = original
        updated = privacy_overwrites(self.channel, {8})
        self.assertFalse(updated[self.everyone].view_channel)
        self.assertFalse(updated[self.support].view_channel)
        self.assertFalse(updated[self.support].send_messages)
        self.assertFalse(updated[outsider].view_channel)
        self.assertIsNone(updated[allowed].view_channel)
        self.assertTrue(updated[self.manager].view_channel)
        self.assertTrue(updated[self.manager].read_message_history)
        self.assertTrue(updated[self.bot_member].attach_files)
        self.assertTrue(original[self.support].view_channel)

    async def test_administrators_are_not_claimed_to_be_hidden(self):
        admin = role(99, admin=True)
        self.guild.roles.append(admin)
        self.visible.add(99)
        await self.cog.validate_log(self.channel, self.settings)

    async def test_apply_only_changes_configured_log_destinations(self):
        transcript = Mock(spec=discord.TextChannel)
        transcript.id, transcript.guild, transcript.overwrites = 12, self.guild, {}
        transcript.permissions_for = self.channel.permissions_for
        self.channel.edit = AsyncMock()
        transcript.edit = AsyncMock()
        unrelated = SimpleNamespace(edit=AsyncMock())
        self.guild.get_channel = Mock(
            side_effect={10: self.channel, 12: transcript, 999: unrelated}.get
        )
        self.cog.validate_log = AsyncMock()
        count = await self.cog.apply_log_access(self.guild, PANEL, self.settings, self.revision)
        self.assertEqual(count, 2)
        for target in (self.channel, transcript):
            target.edit.assert_awaited_once()
            overwrites = target.edit.await_args.kwargs["overwrites"]
            self.assertTrue(overwrites[self.manager].view_channel)
            self.assertNotIn(self.support, overwrites)
        unrelated.edit.assert_not_awaited()

    async def test_stale_settings_prevent_any_permission_changes(self):
        self.guild.get_channel = Mock(return_value=self.channel)
        self.channel.edit = AsyncMock()
        await self.store.configure(1, self.revision, copy.deepcopy(self.config))
        with self.assertRaisesRegex(ValueError, "Settings changed"):
            await self.cog.apply_log_access(self.guild, PANEL, self.settings, self.revision)
        self.channel.edit.assert_not_awaited()

    async def test_apply_never_changes_ticket_or_public_panel_channels(self):
        self.guild.get_channel = Mock(return_value=self.channel)
        self.channel.edit = AsyncMock()
        self.cog.store.by_channel = AsyncMock(return_value={"id": "ticket"})
        with self.assertRaisesRegex(ValueError, "separate log channel"):
            await self.cog.apply_log_access(self.guild, PANEL, self.settings, self.revision)
        self.channel.edit.assert_not_awaited()

    async def test_all_access_pickers_and_log_page_fit_and_go_back_to_access(self):
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        for selected in (None, PANEL, KIND):
            for page in ("access", "log_access"):
                for pick in (
                    None,
                    "support_roles",
                    "approver_roles",
                    "manager_roles",
                    "allowed_roles",
                    "blocked_roles",
                    "log_viewer_roles",
                ):
                    view = Admin(
                        self.cog,
                        parent,
                        self.revision,
                        self.config,
                        page=page,
                        selected=selected,
                        pick=pick,
                        notice="Saved",
                    )
                    self.assertLessEqual(view.total_children_count, 40)
                    self.assertLessEqual(view.content_length(), 4000)
                    view.stop()
