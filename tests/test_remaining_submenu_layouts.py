import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from ky_bot.admin_functions.autodelete import command as autodelete
from ky_bot.admin_functions.autoreact import command as autoreact
from ky_bot.admin_functions.autothread import command as autothread
from ky_bot.askdm.store import defaults as request_defaults
from ky_bot.askdm.views import Admin, uploads
from ky_bot.design.menu_design import default_profile
from ky_bot.leveling import menu as leveling
from ky_bot.leveling.store import defaults as leveling_defaults
from ky_bot.moderation.views import Confirm, MemberForm, artwork
from ky_bot.views.help import GUIDES, HelpView, help_files
from ky_bot.views.menu_strips import category_files


def media_names(view):
    return {
        item.media.url.removeprefix("attachment://")
        for child in view.walk_children()
        if isinstance(child, discord.ui.MediaGallery)
        for item in child.items
    }


class RemainingSubmenuLayoutTests(unittest.IsolatedAsyncioTestCase):
    def assert_artwork(self, view, attachments):
        try:
            self.assertLessEqual(view.total_children_count, 40)
            self.assertLessEqual(len(attachments), 10)
            self.assertTrue(media_names(view).issubset({item.filename for item in attachments}))
            self.assertTrue(any(name.startswith("ky_strip_") for name in media_names(view)))
            view.to_components()
        finally:
            for attachment in attachments:
                attachment.close()
            view.stop()

    async def test_all_automation_private_pages_upload_their_category_artwork(self):
        guild = SimpleNamespace(
            id=1, get_channel=lambda _: None, get_member=lambda _: None, text_channels=[], emojis=[]
        )
        interaction = SimpleNamespace(
            guild=guild,
            guild_id=1,
            user=SimpleNamespace(id=50),
            channel=None,
            channel_id=None,
            edit_original_response=AsyncMock(),
        )
        repository = SimpleNamespace(get_designs=AsyncMock(return_value=(0, {})))
        cog = SimpleNamespace(
            bot=SimpleNamespace(server_settings=SimpleNamespace(repository=repository)),
            store=SimpleNamespace(list=lambda _: []),
        )
        for module in (autodelete, autothread, autoreact):
            session = module.Session(cog, interaction)
            for page in module.PAGE_TITLES:
                with self.subTest(feature=module.__name__, page=page):
                    await session.show(interaction, page)
                    args = interaction.edit_original_response.await_args.kwargs
                    view = args["view"]
                    # The response already closed these attachment handles, as intended.
                    self.assert_artwork(view, args["attachments"])

    async def test_all_leveling_selectors_have_category_artwork(self):
        session = SimpleNamespace(
            owner=50,
            guild=SimpleNamespace(id=1),
            config=leveling_defaults(),
            generation=0,
            command_center=None,
            reward_level=1,
            design=default_profile("settings:leveling"),
        )
        for page, title in leveling.PAGE_TITLES.items():
            with self.subTest(page=page):
                view = leveling.Menu(session, page)
                self.assert_artwork(view, category_files([title], session.design["style"]))

    async def test_dmfr_role_groups_upload_their_own_artwork(self):
        parent = SimpleNamespace(owner_id=50, guild_id=1, panel_manager=None)
        for picker in ("roles", *Admin.ROLE_FIELDS):
            with self.subTest(picker=picker):
                view = Admin(Mock(), parent, "revision", request_defaults(), picker=picker)
                self.assertEqual(view.category_menu, "askdm:roles")
                self.assert_artwork(view, uploads(default_profile("settings:askdm"), view))

    async def test_every_help_guide_has_its_category_images_and_fits(self):
        view = HelpView(50, 1, administrator=True, manage_guild=True)
        for page in ("home", *GUIDES):
            with self.subTest(page=page):
                view.show(page)
                attachments = help_files(view)
                try:
                    self.assertLessEqual(view.total_children_count, 40)
                    self.assertLessEqual(len(attachments), 10)
                    self.assertTrue(
                        media_names(view).issubset({item.filename for item in attachments})
                    )
                    self.assertTrue(any(name.startswith("ky_strip_") for name in media_names(view)))
                finally:
                    for attachment in attachments:
                        attachment.close()
        view.stop()

    async def test_moderation_member_forms_and_reviews_have_categories(self):
        for action in ("ban", "kick", "timeout"):
            with self.subTest(action=action):
                for view in (
                    MemberForm(Mock(), 50, 1, action),
                    Confirm(Mock(), 50, 1, 2, "Member", action, None, "Reason"),
                ):
                    self.assert_artwork(view, artwork(view))

    async def test_appearance_editor_pages_upload_their_category_artwork(self):
        from ky_bot.design.editor import DesignSession

        interaction = SimpleNamespace(
            user=SimpleNamespace(id=50),
            guild_id=1,
            guild=SimpleNamespace(id=1),
            client=SimpleNamespace(),
            edit_original_response=AsyncMock(),
        )
        session = DesignSession(interaction, Mock(), None, {})
        await session.select(["settings:overview"])
        for page in ("edit", "targets", "finish"):
            with self.subTest(page=page):
                await session.show(interaction, page)
                args = interaction.edit_original_response.await_args.kwargs
                self.assert_artwork(args["view"], args["attachments"])
