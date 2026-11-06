import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord import app_commands

from ky_bot.admin_functions.autodelete.command import AutoDelete, Menu, Session


class PurgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_purge_opens_existing_cleanup_for_current_channel_without_deleting(self):
        channel = Mock(spec=discord.TextChannel)
        channel.id = 30
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            user=SimpleNamespace(id=50),
            channel=channel,
            channel_id=30,
            message=None,
            response=SimpleNamespace(is_done=lambda: False, defer=AsyncMock()),
        )
        cog = SimpleNamespace(service=SimpleNamespace(cleanup=AsyncMock()))
        sessions = []

        def create(*args):
            session = Session(*args)
            session.show = AsyncMock()
            sessions.append(session)
            return session

        with patch("ky_bot.admin_functions.autodelete.command.Session", side_effect=create):
            await AutoDelete.purge.callback(cog, interaction)
        session = sessions[0]
        self.assertTrue(session.clean)
        self.assertEqual(session.channels, [30])
        self.assertIsNone(session.command_center)
        session.show.assert_awaited_once_with(interaction, "clean")
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        cog.service.cleanup.assert_not_awaited()

    async def test_cleanup_confirmation_reuses_service_and_does_not_save_a_rule(self):
        channel = Mock(spec=discord.TextChannel)
        channel.id = 30
        member = SimpleNamespace(id=50, guild_permissions=discord.Permissions(manage_guild=True))
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            guild_id=1,
            user=member,
            channel=channel,
            channel_id=30,
            response=SimpleNamespace(defer=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        cog = SimpleNamespace(
            service=SimpleNamespace(cleanup=AsyncMock(return_value=(100, 4, 0))), store=Mock()
        )
        session = Session(cog, interaction)
        session.clean = True
        session.content = ["messages_all"]
        session.destinations = Mock(return_value=[channel])
        session.show = AsyncMock()
        review = Menu(session, "confirm_clean")
        cog.service.cleanup.assert_not_awaited()
        confirm = next(
            item
            for item in review.walk_children()
            if isinstance(item, discord.ui.Button) and item.label == "Delete now"
        )
        await confirm.callback(interaction)
        cog.service.cleanup.assert_awaited_once_with(
            channel, ["messages_all"], 300, 100, member, age_mode="within"
        )
        cog.store.save.assert_not_called()
        self.assertIn("4 messages", interaction.followup.send.await_args.args[0])
        review.stop()

    async def test_purge_requires_manage_server_and_is_server_only(self):
        command = AutoDelete.purge
        self.assertTrue(command.guild_only)
        self.assertTrue(command.default_permissions.manage_guild)
        interaction = SimpleNamespace(permissions=discord.Permissions.none())
        with self.assertRaises(app_commands.MissingPermissions):
            await command.checks[0](interaction)
