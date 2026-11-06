import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from PIL import Image

from ky_bot.admin_functions.settings_view import SettingsView, settings_files
from ky_bot.design.editor import DesignSession
from ky_bot.design.editor import Editor as DesignEditor
from ky_bot.design.menu_design import default_profile
from ky_bot.leveling.menu import Menu as LevelMenu
from ky_bot.leveling.store import defaults
from ky_bot.views.menu_strips import GROUPS, group_sections, heading, strip_files


def strip_titles(view):
    return [
        item.description
        for child in view.walk_children()
        if isinstance(child, discord.ui.MediaGallery)
        for item in child.items
        if "ky_strip_" in item.media.url
    ]


class MenuStripTests(unittest.IsolatedAsyncioTestCase):
    async def test_grouping_moves_pickers_with_their_controls_and_keeps_callbacks(self):
        panel = discord.ui.Container()
        callback = AsyncMock()
        for title in ("WELCOME MESSAGE", "APPEARANCE", "DESTINATION", "DELIVERY"):
            button = discord.ui.Button(label="Edit")
            button.callback = callback
            panel.add_item(discord.ui.Section("**" + title + "**", accessory=button))
            if title == "DESTINATION":
                picker = discord.ui.ChannelSelect()
                picker.callback = callback
                panel.add_item(discord.ui.ActionRow(picker))
        group_sections(panel, "settings:welcome")
        sections = [
            heading(child) for child in panel.children if isinstance(child, discord.ui.Section)
        ]
        self.assertEqual(sections, ["DESTINATION", "DELIVERY", "WELCOME MESSAGE", "APPEARANCE"])
        children = list(panel.children)
        destination = next(i for i, x in enumerate(children) if heading(x) == "DESTINATION")
        self.assertIs(children[destination + 1].children[0], picker)
        await picker.callback("interaction")
        callback.assert_awaited_once_with("interaction")

    async def test_command_center_and_welcome_match_approved_order_and_media(self):
        for section in ("overview", "general", "welcome", "logging"):
            view = SettingsView(50, 1, Mock())
            view.show_section(section)
            self.assertEqual(
                strip_titles(view), [title for title, _ in GROUPS["settings:" + section]]
            )
            self.assertLessEqual(view.total_children_count, 40)
            attachments = settings_files(section)
            try:
                names = {file.filename for file in attachments}
                self.assertLessEqual(len(names), 10)
                for child in view.walk_children():
                    if isinstance(child, discord.ui.MediaGallery):
                        for item in child.items:
                            self.assertIn(item.media.url.removeprefix("attachment://"), names)
            finally:
                for file in attachments:
                    file.close()
            view.stop()

    async def test_leveling_order_and_design_preview_fit_discord_limits(self):
        session = SimpleNamespace(
            owner=50,
            guild=SimpleNamespace(id=1),
            config=defaults(),
            generation=0,
            command_center=None,
            design=default_profile("settings:leveling"),
        )
        sample = LevelMenu(session)
        self.assertEqual(strip_titles(sample), [title for title, _ in GROUPS["leveling"]])
        self.assertLessEqual(sample.total_children_count, 40)
        i = SimpleNamespace(
            user=SimpleNamespace(id=50), guild_id=1, guild=session.guild, client=SimpleNamespace()
        )
        design = DesignSession(i, Mock(), None, {})
        await design.select(["settings:leveling"])
        design.preview_key = "settings:leveling"
        preview = DesignEditor(design, "preview", sample=sample)
        self.assertLessEqual(preview.total_children_count, 40)
        preview.stop()
        sample.stop()

    async def test_command_center_appearance_preview_fits(self):
        i = SimpleNamespace(
            user=SimpleNamespace(id=50),
            guild_id=1,
            guild=SimpleNamespace(),
            client=SimpleNamespace(),
        )
        session = DesignSession(i, Mock(), None, {})
        await session.select(["settings:overview"])
        session.preview_key = "settings:overview"
        sample = SettingsView(50, 1, Mock())
        preview = DesignEditor(session, "preview", sample=sample)
        self.assertLessEqual(preview.total_children_count, 40)
        preview.stop()
        sample.stop()

    async def test_all_finishes_ship_ready_to_use_transparent_strip_images(self):
        for menu in GROUPS:
            for style in ("dark_silver", "gold", "silver_neon"):
                attachments = strip_files(menu, style)
                try:
                    for file in attachments:
                        with Image.open(io.BytesIO(file.fp.read())) as image:
                            self.assertEqual(image.size, (1120, 76))
                            self.assertEqual(image.mode, "RGBA")
                finally:
                    for file in attachments:
                        file.close()

    async def test_repeated_category_labels_are_removed_without_losing_controls(self):
        for menu, title in (
            ("settings:welcome", "APPEARANCE"),
            ("custom_embeds:images", "HEADER IMAGE"),
            ("askdm", "MEMBER PANEL"),
        ):
            with self.subTest(menu=menu):
                panel = discord.ui.Container()
                control = discord.ui.Button(label="Edit")
                control.callback = AsyncMock()
                section = discord.ui.Section(
                    "**" + title + "**\nKeep this description.", accessory=control
                )
                panel.add_item(section)
                group_sections(panel, menu)
                self.assertEqual(section.children[0].content, "Keep this description.")
                self.assertIs(section.accessory, control)
                await control.callback("interaction")
                control.callback.assert_awaited_once_with("interaction")

    async def test_command_center_order_and_roomier_rows_preserve_discord_limits(self):
        expected = ["SERVER SETUP", "COMMUNITY", "TOOLS & DESIGN", "ADMINISTRATION"]
        for manager in (None, Mock()):
            view = SettingsView(50, 1, Mock(), panel_manager=manager)
            self.assertEqual(strip_titles(view), expected)
            self.assertLessEqual(view.total_children_count, 40)
            rows = {
                heading(item): item
                for item in view.children[0].children
                if isinstance(item, discord.ui.Section)
            }
            spaced = {
                "COMMAND CENTER CHANNEL",
                "LEVELING",
                "CONFESSIONS",
                "GENERAL SETTINGS",
                "MODERATION",
            }
            for title in spaced:
                self.assertTrue(rows[title].children[0].content.endswith("\n-# \u200b"), title)
            for title in ("WELCOME EXPERIENCE", "ASK TO DM/FR", "MANAGE EMBEDS", "LOGS"):
                self.assertFalse(rows[title].children[0].content.endswith("\n-# \u200b"), title)
            view.to_components()
            view.stop()
