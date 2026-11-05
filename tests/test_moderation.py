import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from ky_bot.admin_functions.settings_view import SettingsView
from ky_bot.moderation.command import Moderation
from ky_bot.moderation.service import MAX_TIMEOUT, apply, authorize, parse_duration
from ky_bot.moderation.views import Confirm, MemberForm, Menu


class ModerationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        def member(ident, role, bot=False):
            return SimpleNamespace(
                id=ident,
                top_role=role,
                bot=bot,
                display_name=f"Member {ident}",
                guild_permissions=discord.Permissions.all(),
                ban=AsyncMock(),
                kick=AsyncMock(),
                timeout=AsyncMock(),
            )

        self.actor, self.target, self.bot_member = member(1, 10), member(2, 2), member(3, 20, True)
        self.target.guild_permissions = discord.Permissions.none()
        self.members = {m.id: m for m in (self.actor, self.target, self.bot_member)}
        self.guild = SimpleNamespace(
            id=7, owner_id=99, fetch_member=AsyncMock(side_effect=lambda ident: self.members[ident])
        )

    async def test_custom_duration_formats_and_boundary(self):
        for text, seconds in (
            ("30m", 1800),
            ("2h", 7200),
            ("3d 12h", 302400),
            ("1 hour 30 minutes", 5400),
            ("1.5h", 5400),
            ("4w", MAX_TIMEOUT),
            ("1s", 1),
            ("1d, 2H", 93600),
        ):
            self.assertEqual(parse_duration(text), timedelta(seconds=seconds))
        for text in (
            "",
            "30",
            "-1h",
            "0s",
            "29d",
            "28d 1s",
            "1month",
            "1h garbage",
            "1h -2m",
            "0.1s",
            "forever",
            "1e3h",
        ):
            with self.assertRaises(ValueError, msg=text):
                parse_duration(text)

    async def test_actor_and_bot_require_action_specific_permissions(self):
        for action, permission in (
            ("ban", "ban_members"),
            ("kick", "kick_members"),
            ("timeout", "moderate_members"),
        ):
            for member in (self.actor, self.bot_member):
                setattr(member.guild_permissions, permission, False)
                with self.assertRaises(ValueError):
                    await authorize(self.guild, 1, 2, 3, action)
                setattr(member.guild_permissions, permission, True)

    async def test_hierarchy_and_protected_targets(self):
        for member in (self.actor, self.bot_member):
            old = member.top_role
            member.top_role = 2
            with self.assertRaises(ValueError):
                await authorize(self.guild, 1, 2, 3, "ban")
            member.top_role = old
        for ident in (1, 3):
            with self.assertRaises(ValueError):
                await authorize(self.guild, 1, ident, 3, "ban")
        self.guild.owner_id = 2
        with self.assertRaises(ValueError):
            await authorize(self.guild, 1, 2, 3, "ban")

    async def test_owner_can_override_actor_hierarchy_but_not_bot_hierarchy(self):
        self.guild.owner_id = 1
        self.actor.top_role = 1
        await authorize(self.guild, 1, 2, 3, "ban")
        self.bot_member.top_role = 1
        with self.assertRaises(ValueError):
            await authorize(self.guild, 1, 2, 3, "ban")

    async def test_timeout_rejects_administrator_and_bot_targets(self):
        self.target.guild_permissions.administrator = True
        with self.assertRaises(ValueError):
            await authorize(self.guild, 1, 2, 3, "timeout")
        self.target.guild_permissions.administrator = False
        self.target.bot = True
        with self.assertRaises(ValueError):
            await authorize(self.guild, 1, 2, 3, "timeout")

    async def test_ban_has_audit_reason_and_does_not_delete_message_history(self):
        await apply(self.guild, 1, 2, 3, "ban", reason="Repeated violations")
        self.target.ban.assert_awaited_once()
        args = self.target.ban.await_args.kwargs
        self.assertEqual(args["delete_message_seconds"], 0)
        self.assertIn("Moderator 1", args["reason"])
        self.assertIn("Repeated violations", args["reason"])
        self.target.timeout.assert_not_awaited()

    async def test_kick_audit_and_fresh_permissions(self):
        target, until = await apply(self.guild, 1, 2, 3, "kick", reason="Repeated violations")
        self.assertIs(target, self.target)
        self.assertIsNone(until)
        self.target.kick.assert_awaited_once()
        self.assertIn("Moderator 1", self.target.kick.await_args.kwargs["reason"])
        self.assertIn("Repeated violations", self.target.kick.await_args.kwargs["reason"])
        self.target.ban.assert_not_awaited()
        self.target.timeout.assert_not_awaited()
        self.target.kick.reset_mock()
        self.actor.guild_permissions.kick_members = False
        with self.assertRaises(ValueError):
            await apply(self.guild, 1, 2, 3, "kick")
        self.target.kick.assert_not_awaited()

    async def test_kick_hierarchy_and_protected_targets(self):
        for ident in (1, 3):
            with self.assertRaises(ValueError):
                await authorize(self.guild, 1, ident, 3, "kick")
        self.guild.owner_id = 2
        with self.assertRaises(ValueError):
            await authorize(self.guild, 1, 2, 3, "kick")
        self.guild.owner_id = 99
        for member in (self.actor, self.bot_member):
            old = member.top_role
            member.top_role = 2
            with self.assertRaises(ValueError):
                await authorize(self.guild, 1, 2, 3, "kick")
            member.top_role = old

    async def test_kick_confirmation_and_help_for_kick_only_moderator(self):
        from ky_bot.moderation.views import Details
        from ky_bot.views.help import HelpView

        help_view = HelpView(1, 7, kick_members=True)
        self.assertTrue(help_view.moderator)
        help_view.show("moderation")
        self.assertIn("/kick", str(help_view.to_components()))
        self.assertLessEqual(help_view.total_children_count, 40)
        help_view.stop()
        self.assertEqual(Details(Mock(), 1, 7, 2, "kick").title, "Kick details")
        cog = SimpleNamespace(execute=AsyncMock(return_value=(self.target, None)))
        view = Confirm(cog, 1, 7, 2, "Member 2", "kick", None, "Reason")
        self.assertIn("Confirm kick", str(view.to_components()))
        with patch("ky_bot.moderation.views.display", new=AsyncMock()) as display:
            await asyncio.gather(view.confirm(self.interaction()), view.confirm(self.interaction()))
        cog.execute.assert_awaited_once_with(unittest.mock.ANY, 2, "kick", None, "Reason")
        self.assertIn("MEMBER KICKED", str(display.await_args.args[1].to_components()))
        cog.execute.reset_mock()
        view = Confirm(cog, 1, 7, 2, "Member 2", "kick", None, "Reason")
        with patch("ky_bot.moderation.views.display", new=AsyncMock()):
            await view.cancel(self.interaction())
            await view.confirm(self.interaction())
        cog.execute.assert_not_awaited()

    async def test_timeout_is_applied_until_expected_time(self):
        before = discord.utils.utcnow()
        target, until = await apply(self.guild, 1, 2, 3, "timeout", duration=parse_duration("2h"))
        self.assertIs(target, self.target)
        self.assertGreaterEqual(until, before + timedelta(hours=2))
        self.assertLess(until, before + timedelta(hours=2, seconds=5))
        self.target.timeout.assert_awaited_once()
        self.target.ban.assert_not_awaited()

    async def test_permission_change_after_review_prevents_api_call(self):
        await authorize(self.guild, 1, 2, 3, "ban")
        self.actor.guild_permissions.ban_members = False
        with self.assertRaises(ValueError):
            await apply(self.guild, 1, 2, 3, "ban")
        self.target.ban.assert_not_awaited()

    async def test_invalid_duration_never_calls_discord(self):
        for duration in (None, timedelta(0), timedelta(days=29)):
            with self.assertRaises(ValueError):
                await apply(self.guild, 1, 2, 3, "timeout", duration=duration)
        self.target.timeout.assert_not_awaited()

    def interaction(self, user=1, guild=7):
        return SimpleNamespace(
            user=SimpleNamespace(id=user),
            guild_id=guild,
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_confirmation_only_executes_once_and_cancel_never_executes(self):
        cog = SimpleNamespace(execute=AsyncMock(return_value=(self.target, None)))
        view = Confirm(cog, 1, 7, 2, "Member 2", "ban", None, "Reason")
        i = self.interaction()
        with patch("ky_bot.moderation.views.display", new=AsyncMock()):
            await asyncio.gather(view.confirm(i), view.confirm(i))
        cog.execute.assert_awaited_once()
        view = Confirm(cog, 1, 7, 2, "Member 2", "ban", None, "Reason")
        cog.execute.reset_mock()
        with patch("ky_bot.moderation.views.display", new=AsyncMock()):
            await view.cancel(i)
            await view.confirm(i)
        cog.execute.assert_not_awaited()

    async def test_other_member_or_server_cannot_confirm(self):
        cog = SimpleNamespace(execute=AsyncMock())
        view = Confirm(cog, 1, 7, 2, "Member 2", "ban", None, "")
        await view.confirm(self.interaction(user=9))
        await view.confirm(self.interaction(guild=8))
        cog.execute.assert_not_awaited()
        view.stop()

    async def test_uncertain_http_failure_does_not_repeat_action(self):
        error = discord.HTTPException(SimpleNamespace(status=500, reason="Error"), "Failure")
        cog = SimpleNamespace(execute=AsyncMock(side_effect=error))
        view = Confirm(cog, 1, 7, 2, "Member 2", "ban", None, "")
        with patch("ky_bot.moderation.views.display", new=AsyncMock()) as display:
            await view.confirm(self.interaction())
            await view.confirm(self.interaction())
        cog.execute.assert_awaited_once()
        self.assertIn("audit log", str(display.await_args.args[1].to_components()))

    async def test_slash_options_and_defaults(self):
        self.assertTrue(Moderation.kick.default_permissions.kick_members)
        self.assertEqual([p.name for p in Moderation.kick.parameters], ["member", "reason"])
        self.assertFalse(Moderation.kick.parameters[1].required)
        self.assertTrue(Moderation.ban.default_permissions.ban_members)
        self.assertTrue(Moderation.timeout.default_permissions.moderate_members)
        self.assertEqual([p.name for p in Moderation.ban.parameters], ["member", "reason"])
        params = Moderation.timeout.parameters
        self.assertEqual([p.name for p in params], ["member", "duration", "reason"])
        self.assertTrue(params[0].required and params[1].required)
        self.assertFalse(params[2].required)

    async def test_menus_and_command_center_fit_discord_limit(self):
        cog = Mock()
        parent = SimpleNamespace(owner_id=1, guild_id=7, panel_manager=None)
        for view in (
            Menu(cog, parent),
            MemberForm(cog, 1, 7, "kick"),
            Confirm(cog, 1, 7, 2, "Member 2", "kick", None, "Reason"),
            MemberForm(cog, 1, 7, "ban"),
            MemberForm(cog, 1, 7, "timeout"),
            Confirm(cog, 1, 7, 2, "Member 2", "timeout", parse_duration("28d"), "Reason"),
        ):
            self.assertLessEqual(view.total_children_count, 40)
            view.to_components()
            view.stop()
        settings = SettingsView(1, 7, Mock())
        for picker in (False, "command_center"):
            settings.show_section("overview", picker=picker)
            self.assertIn("MODERATION", str(settings.to_components()))
            self.assertLessEqual(settings.total_children_count, 40)
        settings.stop()


if __name__ == "__main__":
    unittest.main()
