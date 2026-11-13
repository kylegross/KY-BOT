import copy
import re
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from ky_bot.tickets.artwork import public_files
from ky_bot.tickets.command import Tickets
from ky_bot.tickets.opening import OpenedPanel, example_ticket, opening_design
from ky_bot.tickets.sections import new_section
from ky_bot.tickets.store import defaults, effective, new_panel, new_type, validate
from ky_bot.tickets.views import Admin, TicketButton

PANEL = "a" * 32
KIND = "b" * 32


def config():
    value = defaults()
    value["panels"][PANEL] = new_panel("Support", 123) | dict(types=[KIND])
    value["types"][KIND] = new_type("Support")
    return value


class OpeningPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_opening_editor_saves_only_nested_design(self):
        value = config()
        before = copy.deepcopy(value)
        cog = SimpleNamespace(store=SimpleNamespace(configure=AsyncMock()), admin=AsyncMock())
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        menu = Admin(
            cog, parent, "revision", value, page="text_before", selected=PANEL, opening=True
        )
        interaction = SimpleNamespace(
            response=SimpleNamespace(is_done=Mock(return_value=True), defer=AsyncMock())
        )
        await menu.set_text_section(interaction, "before", {"heading": "Welcome"})
        saved = cog.store.configure.await_args.args[2]
        self.assertEqual(saved["panels"][PANEL]["channel"], 123)
        self.assertEqual(saved["panels"][PANEL]["before_heading"], "")
        self.assertEqual(saved["panels"][PANEL]["opening_panel"]["before_heading"], "Welcome")
        self.assertTrue(cog.admin.await_args.kwargs["opening"])
        self.assertEqual(value, before)
        validate(saved)
        menu.stop()

    async def test_controls_follow_lifecycle_and_persist_claim(self):
        design = opening_design({})
        for state, expected in (
            ("waiting_staff", ["claim", "close", "transcript", "manage"]),
            ("pending_approval", ["manage", "approve", "decline", "transcript"]),
            ("closed", ["manage", "reopen", "transcript"]),
        ):
            ticket = example_ticket(design) | dict(state=state)
            view = OpenedPanel(design, ticket)
            controls = [item for item in view.walk_children() if isinstance(item, TicketButton)]
            self.assertEqual([item.action for item in controls], expected)
            for item in controls:
                self.assertIsNotNone(
                    re.fullmatch(TicketButton.__discord_ui_compiled_template__, item.item.custom_id)
                )
            view.stop()

    async def test_styles_sections_and_artwork_match_editor(self):
        design = opening_design({})
        design.update(
            before_heading="WELCOME",
            before_heading_style="decorative",
            after_text="Please wait for staff.",
            after_style="italic",
            after_sections=[new_section() | dict(heading="Privacy", body="Private ticket.")],
        )
        value = config()
        value["panels"][PANEL]["opening_panel"] = design
        validate(value)
        view = OpenedPanel(design, example_ticket(design), preview=True)
        files = public_files(design)
        try:
            self.assertIn("ticket_panel_before_heading.png", [file.filename for file in files])
            self.assertIn("Privacy", str(view.to_components()))
            self.assertTrue(
                all(
                    item.item.disabled
                    for item in view.walk_children()
                    if isinstance(item, TicketButton)
                )
            )
            self.assertEqual(
                sum(isinstance(item, discord.ui.Separator) for item in view.walk_children()), 1
            )
        finally:
            for file in files:
                file.close()
            view.stop()

    async def test_new_ticket_gets_independent_design_snapshot_and_legacy_welcome(self):
        value = config()
        value["defaults"]["welcome"] = "Legacy welcome"
        settings = effective(value, KIND, PANEL)
        self.assertEqual(settings["opening_panel"]["before_text"], "Legacy welcome")
        value["panels"][PANEL]["opening_panel"] = opening_design({})
        value["panels"][PANEL]["opening_panel"]["title"] = "New title"
        self.assertNotEqual(settings["opening_panel"]["title"], "New title")

    async def test_sender_and_refresh_keep_layout_and_attachments(self):
        design = opening_design({})
        ticket = example_ticket(design)
        ticket.update(answers=[("Question", "Answer")], intro_message=33)
        ticket["settings"].update(ping_staff=False, support_roles=[])
        cog = object.__new__(Tickets)
        cog.store = SimpleNamespace(update=AsyncMock())
        message = SimpleNamespace(edit=AsyncMock())
        channel = SimpleNamespace(
            send=AsyncMock(return_value=SimpleNamespace(id=33)),
            get_partial_message=Mock(return_value=message),
        )
        await cog.send_intro(ticket, channel)
        self.assertIsInstance(channel.send.await_args_list[0].kwargs["view"], OpenedPanel)
        self.assertEqual(channel.send.await_args_list[1].kwargs["embed"].title, "Ticket intake")
        await cog.refresh_intro(ticket | dict(state="closed"), channel)
        self.assertIsInstance(message.edit.await_args.kwargs["view"], OpenedPanel)
        self.assertNotIn("attachments", message.edit.await_args.kwargs)

    async def test_opening_limits_and_root_navigation(self):
        value = config()
        design = opening_design({})
        design["before_sections"] = [new_section()] * 4
        value["panels"][PANEL]["opening_panel"] = design
        with self.assertRaises(ValueError):
            validate(value)
        value = config()
        cog = SimpleNamespace(admin=AsyncMock())
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        menu = Admin(cog, parent, "revision", value, page="panel", selected=PANEL, opening=True)
        event = SimpleNamespace(response=SimpleNamespace(defer=AsyncMock()))
        await menu.go("panels")(event)
        self.assertEqual(cog.admin.await_args.kwargs["selected"], PANEL)
        self.assertNotIn("opening", cog.admin.await_args.kwargs)
        menu.stop()
