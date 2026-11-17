import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

from ky_bot.advice.command import Advice
from ky_bot.advice.panels import submission_panel
from ky_bot.advice.store import defaults


class AdviceCommandTests(unittest.IsolatedAsyncioTestCase):
    def interaction(self, channel_id=10, roles=(30,)):
        targets = {
            n: SimpleNamespace(
                id=n, name=f"ask-{n}", permissions_for=lambda _: SimpleNamespace(view_channel=True)
            )
            for n in (10, 11)
        }
        return SimpleNamespace(
            guild_id=1,
            channel_id=channel_id,
            channel=SimpleNamespace(),
            guild=SimpleNamespace(get_channel=targets.get),
            user=SimpleNamespace(id=7, roles=[SimpleNamespace(id=n) for n in roles]),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    def cog(self):
        config = defaults() | {"enabled": True, "access_role": 30}
        return SimpleNamespace(
            begin=AsyncMock(),
            store=SimpleNamespace(
                channels=AsyncMock(return_value=[(1, 10, config), (1, 11, config)])
            ),
        )

    async def test_current_channel_and_explicit_channel_use_existing_private_flow(self):
        cog = self.cog()
        i = self.interaction()
        await Advice.open_advice(cog, i)
        cog.begin.assert_awaited_once_with(i, 10)
        await Advice.open_advice(cog, i, SimpleNamespace(id=11))
        self.assertEqual(cog.begin.await_args.args, (i, 11))
        self.assertEqual(Advice.advice.name, "advice")
        self.assertFalse(Advice.advice.parameters[0].required)

    async def test_multiple_accessible_channels_offer_private_advice_picker(self):
        cog = self.cog()
        i = self.interaction(99)
        await Advice.open_advice(cog, i)
        args = i.response.send_message.await_args.kwargs
        self.assertTrue(args["ephemeral"])
        view = args["view"]
        picker = next(item for item in view.children if isinstance(item, discord.ui.Select))
        self.assertEqual(picker.placeholder, "Choose your advice channel")
        self.assertEqual([item.value for item in picker.options], ["10", "11"])
        cog.begin.assert_not_awaited()
        view.stop()

    async def test_unjoined_members_cannot_choose_a_hidden_destination(self):
        cog = self.cog()
        with self.assertRaisesRegex(ValueError, "Join an Ask for Advice channel first"):
            await Advice.open_advice(cog, self.interaction(99, roles=()))
        cog.begin.assert_not_awaited()

    async def test_instruction_and_button_render_below_description(self):
        with (
            patch("ky_bot.advice.panels.artwork_bytes", return_value=b"art"),
            patch("ky_bot.advice.panels.heading_image", return_value=b"heading"),
        ):
            view = submission_panel(10, defaults(), preview=True)
        items = view.panel.children
        text_index = next(
            n for n, item in enumerate(items) if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertIn("\n\nUse the button below or `/advice` command.", items[text_index].content)
        self.assertIsInstance(items[text_index + 1], discord.ui.ActionRow)
        self.assertEqual(items[text_index + 1].children[0].action, "ask")
        self.assertTrue(items[text_index + 1].children[0].item.disabled)
        self.assertFalse(any(isinstance(item, discord.ui.Section) for item in items))
        view.to_components()
        view.stop()
