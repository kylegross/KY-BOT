import unittest
from unittest.mock import AsyncMock

import discord

from ky_bot.views.menu_strips import group_sections
from ky_bot.views.sections import section_dividers


class NavigationLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_embeds_is_plain_bold_navigation_with_one_regular_divider(self):
        panel = discord.ui.Container()
        panel.add_item(
            discord.ui.Section(
                "**DRAFT NAME**\nExample", accessory=discord.ui.Button(label="Rename")
            )
        )
        saved = discord.ui.Section(
            "**SAVED EMBEDS**\nYour drafts are saved automatically.",
            accessory=discord.ui.Button(label="All embeds"),
        )
        saved.accessory.callback = AsyncMock()
        panel.add_item(saved)
        group_sections(panel, "custom_embeds")
        section_dividers(panel)
        children = list(panel.children)
        self.assertEqual(saved.children[0].content.split("\n")[0], "**SAVED EMBEDS**")
        divider = children[children.index(saved) - 1]
        self.assertIsInstance(divider, discord.ui.Separator)
        self.assertTrue(divider.visible)
        self.assertEqual(divider.spacing, discord.SeparatorSpacing.small)
        self.assertFalse(
            any(
                isinstance(child, discord.ui.MediaGallery)
                and any(item.description == "SAVED EMBEDS" for item in child.items)
                for child in children
            )
        )
        await saved.accessory.callback("interaction")
        saved.accessory.callback.assert_awaited_once_with("interaction")

    async def test_every_dropdown_type_stays_adjacent_to_its_description(self):
        pickers = (
            discord.ui.Select(options=[discord.SelectOption(label="Example")]),
            discord.ui.ChannelSelect(),
            discord.ui.RoleSelect(),
            discord.ui.UserSelect(),
            discord.ui.MentionableSelect(),
        )
        for picker in pickers:
            with self.subTest(picker=type(picker).__name__):
                panel = discord.ui.Container()
                description = discord.ui.TextDisplay("**DESTINATION**\nChoose a channel.")
                row = discord.ui.ActionRow(picker)
                panel.add_item(description)
                panel.add_item(
                    discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.large)
                )
                panel.add_item(row)
                section_dividers(panel)
                self.assertEqual(list(panel.children), [description, row])

    async def test_home_keeps_right_hand_accessory_and_a_plain_bold_label(self):
        panel = discord.ui.Container()
        panel.add_item(discord.ui.TextDisplay("Menu settings."))
        home = discord.ui.Button(label="Home")
        panel.add_item(discord.ui.ActionRow(home))
        section_dividers(panel)
        children = list(panel.children)
        self.assertIsInstance(children[-1], discord.ui.Section)
        self.assertIs(children[-1].accessory, home)
        self.assertEqual(
            children[-1].children[0].content, "**COMMAND CENTER**\nReturn to the main menu."
        )
        self.assertIsInstance(children[-2], discord.ui.Separator)
        self.assertTrue(children[-2].visible)

    async def test_footer_artwork_stays_below_navigation(self):
        panel = discord.ui.Container()
        panel.add_item(discord.ui.TextDisplay("Menu settings."))
        footer = discord.ui.MediaGallery(
            discord.MediaGalleryItem("attachment://ky_settings_footer.png")
        )
        panel.add_item(footer)
        panel.add_item(discord.ui.ActionRow(discord.ui.Button(label="Home")))
        section_dividers(panel)
        self.assertIs(panel.children[-1], footer)
        self.assertIsInstance(panel.children[-2], discord.ui.Section)
        self.assertEqual(panel.children[-2].accessory.label, "Home")
