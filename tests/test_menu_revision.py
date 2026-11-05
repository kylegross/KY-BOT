import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from ky_bot.admin_functions.settings_repository import open_settings
from ky_bot.askdm.command import AskDM
from ky_bot.askdm.views import PublicPanel
from ky_bot.checklists.command import Checklists
from ky_bot.design.editor import TARGETS, DesignSession
from ky_bot.design.menu_design import default_profile


class MenuRevisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_askdm_saved_design_is_server_scoped_and_used_by_sends(self):
        with tempfile.TemporaryDirectory() as directory:
            async with open_settings(Path(directory) / "settings.sqlite3") as repository:
                revision, _ = await repository.get_designs(1)
                profile = default_profile("askdm") | dict(style="gold", title="ASK FIRST")
                await repository.apply_designs(1, {"askdm": profile}, revision)
                cog = AskDM(SimpleNamespace(server_settings=SimpleNamespace(repository=repository)))
                channel = SimpleNamespace(guild=SimpleNamespace(id=1), send=AsyncMock())
                view = PublicPanel(cog)
                seen = []

                def images(profile=None):
                    seen.append(profile)
                    return [discord.File(io.BytesIO(b"image"), filename="ask_header.png")]

                with patch("ky_bot.askdm.command.uploads", side_effect=images):
                    await cog.send_panel(channel, view)
                    files = await cog.appearance(2)
                    for file in files:
                        file.close()
                self.assertEqual(seen[0]["title"], "ASK FIRST")
                self.assertIsNone(seen[1])
                self.assertEqual(view.panel.to_component_dict()["accent_color"], 0xE8C47C)
                self.assertFalse(channel.send.await_args.kwargs["allowed_mentions"].everyone)
                view.stop()

    async def test_askdm_preview_disables_controls_and_uses_draft_artwork(self):
        self.assertIn("askdm", TARGETS)
        ask = AskDM(SimpleNamespace())
        bot = SimpleNamespace(get_cog=lambda name: ask)
        i = SimpleNamespace(
            user=SimpleNamespace(id=50),
            guild_id=1,
            guild=SimpleNamespace(),
            client=bot,
            edit_original_response=AsyncMock(),
        )
        s = DesignSession(i, Mock(), None, {})
        await s.select(["askdm"])
        s.change(title="ASK FIRST", style="gold")
        with patch("ky_bot.askdm.views.uploads", return_value=[]):
            await s.preview(i)
        view = i.edit_original_response.await_args.kwargs["view"]
        buttons = [x for x in view.walk_children() if isinstance(x, discord.ui.Button)]
        request_buttons = [x for x in buttons if x.label in {"Ask to DM/FR", "My requests"}]
        self.assertTrue(request_buttons)
        self.assertTrue(all(x.disabled for x in request_buttons))
        self.assertLessEqual(view.total_children_count, 40)
        view.stop()

    async def test_askdm_design_refresh_edits_existing_panel_without_new_post(self):
        cog = AskDM(SimpleNamespace())
        cog.store = SimpleNamespace(
            config=AsyncMock(return_value=(None, dict(panel=777, panel_channel=10, channel=10)))
        )
        message = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(get_partial_message=Mock(return_value=message), send=AsyncMock())
        cog.channel = AsyncMock(return_value=channel)
        cog.appearance = AsyncMock(return_value=[])
        await cog.refresh_design(SimpleNamespace(id=1))
        channel.get_partial_message.assert_called_once_with(777)
        message.edit.assert_awaited_once()
        channel.send.assert_not_awaited()

    async def test_create_embed_is_registered_in_shared_create_group_and_admin_only(self):
        command = Checklists.create.get_command("embed")
        self.assertIsNotNone(command)
        self.assertTrue(command.default_permissions.administrator)
        self.assertTrue(command.checks)
        for check in command.checks:
            self.assertTrue(
                await discord.utils.maybe_coroutine(
                    check, SimpleNamespace(permissions=discord.Permissions(administrator=True))
                )
            )
            with self.assertRaises(discord.app_commands.MissingPermissions):
                await discord.utils.maybe_coroutine(
                    check, SimpleNamespace(permissions=discord.Permissions.none())
                )
        cog = SimpleNamespace(open_editor=AsyncMock())
        parent = SimpleNamespace(bot=SimpleNamespace(get_cog=lambda name: cog))
        i = SimpleNamespace()
        await command.callback(parent, i)
        cog.open_editor.assert_awaited_once_with(i)
