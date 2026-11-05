import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from ky_bot.views.help import GUIDES, HelpView, help_files


class HelpTests(unittest.IsolatedAsyncioTestCase):
    async def test_pages_fit_and_member_menu_has_no_admin_guides(self):
        for admin, manage in ((False, False), (False, True), (True, True)):
            view = HelpView(1, 2, administrator=admin, manage_guild=manage)
            payload = str(view.to_components())
            self.assertNotIn("Planned", payload)
            self.assertEqual("COMMAND CENTER" in payload, admin)
            self.assertEqual("AUTOMATION & SETUP" in payload, manage)
            for page in ("home", *GUIDES):
                view.show(page)
                self.assertLessEqual(view.total_children_count, 40)
                self.assertLessEqual(view.content_length(), 4000)
                view.to_components()
            view.stop()

    async def test_ownership_and_current_permissions(self):
        view = HelpView(1, 2, administrator=True)
        i = SimpleNamespace(
            user=SimpleNamespace(id=99),
            guild_id=2,
            response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
        )
        self.assertFalse(await view.interaction_check(i))
        i.user.id = 1
        i.guild_id = 99
        self.assertFalse(await view.interaction_check(i))
        i.guild_id = 2
        i.permissions = discord.Permissions.none()
        self.assertTrue(await view.interaction_check(i))
        view.show("admin")
        self.assertEqual(view.page, "home")
        view.stop()

    async def test_navigation_keeps_artwork_and_timeout_disables_nested_buttons(self):
        view = HelpView(1, 2)
        i = SimpleNamespace(
            response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock()
        )
        item = next(x for x in view.walk_children() if isinstance(x, discord.ui.Button))
        await item.callback(i)
        self.assertEqual(view.page, "requests")
        i.edit_original_response.assert_awaited_once()
        args = i.edit_original_response.await_args.kwargs
        self.assertIs(args["view"], view)
        self.assertTrue(args["attachments"])
        names = {attachment.filename for attachment in args["attachments"]}
        for child in view.walk_children():
            if isinstance(child, discord.ui.MediaGallery):
                for item in child.items:
                    self.assertIn(item.media.url.removeprefix("attachment://"), names)
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        buttons = [x for x in view.walk_children() if isinstance(x, discord.ui.Button)]
        self.assertTrue(buttons)
        self.assertTrue(all(x.disabled for x in buttons))
        view.stop()

    async def test_close_uses_v2_text_without_embeds_or_content(self):
        view = HelpView(1, 2)
        i = SimpleNamespace(response=SimpleNamespace(edit_message=AsyncMock()))
        await view.close_menu(i)
        args = i.response.edit_message.await_args.kwargs
        self.assertEqual(args["attachments"], [])
        self.assertIsInstance(args["view"], discord.ui.LayoutView)
        self.assertNotIn("content", args)
        self.assertTrue(view.is_finished())

    async def test_packaged_artwork_available(self):
        attachments = help_files()
        try:
            self.assertEqual(len(attachments), 4)
            self.assertTrue(all(f.fp.read(8) == b"\x89PNG\r\n\x1a\n" for f in attachments))
        finally:
            for f in attachments:
                f.close()


if __name__ == "__main__":
    unittest.main()
