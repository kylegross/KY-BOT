"""Regression coverage for post-apply checklist appearance notices."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from ky_bot.design.editor import DesignSession, Editor


class ChecklistStyleLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=1),
            guild_id=1,
            guild=SimpleNamespace(),
            client=SimpleNamespace(),
        )
        self.session = DesignSession(interaction, Mock(), None, {})
        await self.session.select(["checklist"])

    async def test_post_apply_notice_preserves_every_control_and_upload(self):
        session = self.session
        session.change(
            header_background="uploaded-header",
            footer_background="uploaded-footer",
            footer_icon="uploaded-icon",
            style="gold",
        )
        original = dict(session.drafts["checklist"])
        baseline = Editor(session)
        labels = [x.label for x in baseline.walk_children() if isinstance(x, discord.ui.Button)]
        baseline.stop()
        for _ in range(3):
            interaction = SimpleNamespace(edit_original_response=AsyncMock())
            await session.show(
                interaction, notice="Design applied. Existing checklists refresh automatically."
            )
            view = interaction.edit_original_response.call_args.kwargs["view"]
            self.assertLessEqual(view.total_children_count, 40)
            self.assertEqual(view.total_children_count, len(list(view.walk_children())))
            self.assertEqual(
                labels, [x.label for x in view.walk_children() if isinstance(x, discord.ui.Button)]
            )
            self.assertIn("Design applied.", str(view.to_components()))
            self.assertEqual(session.drafts["checklist"], original)
            view.stop()

    async def test_all_editor_pages_fit_with_notice(self):
        for page in ("edit", "targets", "finish"):
            view = Editor(self.session, page, notice="Draft updated.")
            self.assertLessEqual(view.total_children_count, 40)
            view.to_components()
            view.stop()

    async def test_checklist_and_menu_targets_cannot_be_saved_together(self):
        session = self.session
        with self.assertRaises(ValueError):
            await session.select(["checklist", "settings:general"])
        self.assertEqual(session.selected, ["checklist"])
        session.selected = ["checklist", "settings:general"]
        with self.assertRaises(ValueError):
            await session.apply(SimpleNamespace())
        session.service.repository.apply_designs.assert_not_called()

    async def test_checklist_apply_preserves_every_non_checklist_profile(self):
        session = self.session
        original = {
            "settings:general": {"style": "dark_silver", "footer_text": "Keep me"},
            "askdm": {"style": "dark_silver", "height": 280},
        }
        session.profiles = original
        session.service.repository.apply_designs = AsyncMock(return_value="next")
        session.show = AsyncMock()
        session.change(style="gold", footer_text="Checklist only")
        with patch("ky_bot.design.menu_design.render_menu", return_value=(b"header", b"footer")):
            await session.apply(SimpleNamespace(user=SimpleNamespace(id=1)))
        saved = session.service.repository.apply_designs.await_args.args[1]
        self.assertEqual(saved["settings:general"], original["settings:general"])
        self.assertEqual(saved["askdm"], original["askdm"])
        self.assertEqual(saved["checklist"]["footer_text"], "Checklist only")

    async def test_picker_clears_previous_menu_selections_when_checklist_added(self):
        session = self.session
        await session.select(["settings:general"])
        view = Editor(session, "targets")
        picker = next(x for x in view.walk_children() if isinstance(x, discord.ui.Select))
        picker._values = ["settings:general", "checklist"]
        session.show = AsyncMock()
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=1),
            guild_id=1,
            permissions=SimpleNamespace(administrator=True),
            response=SimpleNamespace(defer=AsyncMock()),
        )
        await picker.callback(interaction)
        self.assertEqual(session.selected, ["checklist"])
        view.stop()
