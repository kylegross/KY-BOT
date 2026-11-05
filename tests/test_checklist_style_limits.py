"""Regression coverage for post-apply checklist appearance notices."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
