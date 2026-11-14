import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from ky_bot.log_style import PREFIX, SUFFIX
from ky_bot.tickets.store import defaults, new_panel, new_type
from ky_bot.tickets.views import Admin, Confirm


class TicketNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_type_list_back_returns_to_originating_panel(self):
        config = defaults()
        config["panels"]["a" * 32] = new_panel("First")
        config["panels"]["b" * 32] = new_panel("Second")
        config["types"]["c" * 32] = new_type("Applications")
        cog = SimpleNamespace(admin=AsyncMock())
        parent = SimpleNamespace(panel_manager=None)
        event = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()))
        # Enter a shared type editor through the second panel.
        menu = Admin(cog, parent, "revision", config, page="panel_type_edit", selected="b" * 32)
        await menu.show(event, "type_appearance", "c" * 32)
        self.assertEqual(cog.admin.await_args.kwargs["panel_context"], "b" * 32)
        menu.stop()
        menu = Admin(cog, parent, "revision", config, page="types", panel_context="b" * 32)
        back = next(item for item in menu.walk_children() if getattr(item, "label", None) == "Back")
        await back.callback(event)
        self.assertEqual(cog.admin.await_args.kwargs["page"], "panel")
        self.assertEqual(cog.admin.await_args.kwargs["selected"], "b" * 32)
        menu.stop()

    async def test_global_type_list_back_still_returns_home(self):
        cog = SimpleNamespace(admin=AsyncMock())
        menu = Admin(cog, SimpleNamespace(panel_manager=None), "revision", defaults(), page="types")
        back = next(item for item in menu.walk_children() if getattr(item, "label", None) == "Back")
        await back.callback(SimpleNamespace(response=SimpleNamespace(defer=AsyncMock())))
        self.assertEqual(cog.admin.await_args.kwargs["page"], "home")
        self.assertIsNone(cog.admin.await_args.kwargs["panel_context"])
        menu.stop()

    async def test_confirmation_uses_owner_log_decoration_and_right_cancel(self):
        ticket = dict(id="a" * 32, owner=123, guild=1, owner_username="example.user")
        for action in ("close", "decline", "delete"):
            menu = Confirm(SimpleNamespace(), ticket, 456, action, "Resolved")
            title = menu.panel.children[0].content
            self.assertIn(PREFIX + action.upper() + " TICKET", title)
            self.assertIn("example.user?" + SUFFIX, title)
            self.assertNotIn("aaaaaaaa", title)
            cancel = next(
                c
                for c in menu.panel.children
                if isinstance(c, discord.ui.Section) and c.accessory.label == "Cancel"
            )
            self.assertEqual(cancel.accessory.callback, menu.cancel)
            menu.stop()

    async def test_legacy_confirmation_resolves_owner_name(self):
        cog = SimpleNamespace(
            bot=SimpleNamespace(get_user=lambda owner: SimpleNamespace(name="legacy.user"))
        )
        menu = Confirm(cog, dict(id="a" * 32, owner=123, guild=1), 456, "close", "Resolved")
        self.assertIn("legacy.user", menu.panel.children[0].content)
        menu.stop()
