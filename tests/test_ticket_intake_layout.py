import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
from ky_bot.tickets.opening import OpenedPanel, example_ticket, opening_design, validate_templates
from ky_bot.tickets.store import defaults, new_panel
from ky_bot.tickets.views import Admin, TicketButton


class IntakeLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_submitted_message_is_inside_panel_between_welcome_and_buttons(self):
        design = opening_design({})
        ticket = example_ticket(design) | dict(
            owner=123456789012345678, owner_username="example.user"
        )
        view = OpenedPanel(design, ticket)
        children = list(view.panel.children)
        index = next(
            i
            for i, child in enumerate(children)
            if isinstance(child, discord.ui.TextDisplay) and "## Hi!" in child.content
        )
        self.assertIsInstance(children[index - 1], discord.ui.Separator)
        self.assertTrue(
            children[index].content.startswith("example.user (123456789012345678)\n## ")
        )
        self.assertIn("Thanks for contacting staff", children[index - 2].content)
        self.assertIsInstance(children[index + 1], discord.ui.ActionRow)
        view.stop()

    async def test_custom_text_styles_buttons_and_legacy_design_merge(self):
        design = opening_design({})
        design.update(
            info_title="Case {number}: {type}",
            info_status="",
            intake_identity="{username} · {user_id}",
            intake_response_style="italic",
            control_labels={
                "transcript": {"label": "Download Record", "emoji": "📄", "style": "blue"}
            },
        )
        validate_templates(design)
        view = OpenedPanel(design, example_ticket(design))
        text = str(view.to_components())
        self.assertIn("Case 1: Support", text)
        self.assertNotIn("Opened by", text)
        self.assertIn("*Hi! I would like", text)
        control = next(
            c
            for c in view.walk_children()
            if isinstance(c, TicketButton) and c.action == "transcript"
        )
        self.assertEqual(control.item.label, "Download Record")
        self.assertEqual(control.item.style, discord.ButtonStyle.primary)
        view.stop()
        old = {
            k: v for k, v in opening_design({}).items() if not k.startswith(("info_", "intake_"))
        }
        view = OpenedPanel(old, example_ticket(old))
        self.assertIn("Ticket No. 1", str(view.to_components()))
        view.stop()

    async def test_intake_is_escaped_and_long_content_fails_before_sending(self):
        design = opening_design({})
        ticket = example_ticket(design) | dict(answers=[("Question", "**fake**\n@everyone")])
        view = OpenedPanel(design, ticket)
        text = str(view.to_components())
        self.assertIn("\\\\*", text)
        self.assertNotIn("@everyone", text)
        view.stop()
        with self.assertRaises(ValueError):
            OpenedPanel(design, ticket | dict(answers=[("Q", "x" * 5000)]))
        with self.assertRaises(ValueError):
            validate_templates(design | dict(info_title="{owner.__class__}"))

    async def test_new_editors_fit_and_keep_opening_panel_context(self):
        config = defaults()
        config["panels"]["a" * 32] = new_panel("Support")
        cog = SimpleNamespace(admin=AsyncMock())
        for page in ("opening_details", "opening_formats", "opening_buttons"):
            menu = Admin(
                cog,
                SimpleNamespace(panel_manager=None),
                "r",
                config,
                page=page,
                selected="a" * 32,
                opening=True,
                pick="intake_response_style",
            )
            self.assertLessEqual(len(list(menu.walk_children())), 40)
            back = next(c for c in menu.walk_children() if getattr(c, "label", None) == "Back")
            await back.callback(SimpleNamespace(response=SimpleNamespace(defer=AsyncMock())))
            self.assertEqual(cog.admin.await_args.kwargs["selected"], "a" * 32)
            self.assertTrue(cog.admin.await_args.kwargs["opening"])
            menu.stop()
