import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord

from ky_bot.admin_functions.command_center import CommandCenterPanels, Panel
from ky_bot.admin_functions.settings_repository import GuildSettings
from ky_bot.admin_functions.settings_view import SettingsView
from ky_bot.confessions.logs import LogMenu, Logs
from ky_bot.nsfw.command import Menu as FlashMenu
from ky_bot.views.menu_context import menu_parent


def control(view, label):
    return next(
        item
        for item in view.walk_children()
        if isinstance(item, discord.ui.Button) and item.label == label
    )


class MenuRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.permissions = discord.Permissions(administrator=True)
        self.guild = SimpleNamespace(
            id=1,
            fetch_member=AsyncMock(
                return_value=SimpleNamespace(guild_permissions=self.permissions)
            ),
            get_channel=lambda _: None,
        )
        self.repository = SimpleNamespace(
            get=AsyncMock(return_value=GuildSettings(1)),
            get_designs=AsyncMock(return_value=(0, {})),
        )
        self.service = SimpleNamespace(repository=self.repository)
        self.bot = SimpleNamespace(
            server_settings=self.service, get_guild=lambda _: self.guild, get_cog=lambda _: None
        )
        self.manager = CommandCenterPanels(self.bot)
        self.parent = SettingsView(
            50, 1, self.service, panel_manager=self.manager, panel_token="token"
        )
        self.parent.guild = self.guild
        self.message = SimpleNamespace(id=100, channel=SimpleNamespace(id=10))
        self.message.edit = AsyncMock(return_value=self.message)
        self.manager.entries[1] = Panel(self.message, self.parent, "token", time.monotonic(), 50)

    async def asyncTearDown(self):
        self.manager.close()

    def interaction(self, private=False):
        return SimpleNamespace(
            user=SimpleNamespace(id=50, guild_permissions=self.permissions),
            guild_id=1,
            guild=self.guild,
            permissions=self.permissions,
            channel=None,
            channel_id=10,
            message=SimpleNamespace(flags=discord.MessageFlags(ephemeral=private)),
            response=SimpleNamespace(
                is_done=lambda: False,
                defer=AsyncMock(),
                send_message=AsyncMock(),
                edit_message=AsyncMock(),
            ),
            edit_original_response=AsyncMock(),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def test_shared_logs_server_activity_back_and_home_stay_on_shared_panel(self):
        interaction = self.interaction()
        await Logs(self.bot).open_menu(interaction, command_center=self.parent)
        interaction.response.defer.assert_awaited_once_with()
        interaction.edit_original_response.assert_not_awaited()
        logs = self.manager.entries[1].view
        self.assertIsInstance(logs, LogMenu)
        activity = next(
            item
            for item in logs.walk_children()
            if isinstance(item, discord.ui.Section)
            and item.children[0].content.startswith("**SERVER EVENTS & MEMBERS**")
        )
        await activity.accessory.callback(self.interaction())
        settings = self.manager.entries[1].view
        self.assertIsInstance(settings, SettingsView)
        self.assertEqual(settings.section, "logging")
        labels = {
            item.label for item in settings.walk_children() if isinstance(item, discord.ui.Button)
        }
        self.assertIn("Back to Logs", labels)
        self.assertIn("Home", labels)
        self.assertNotIn("Refresh", labels)
        self.assertNotIn("Back", labels)
        self.assertTrue(logs.is_finished())
        await control(settings, "Back to Logs").callback(self.interaction())
        restored = self.manager.entries[1].view
        self.assertIsInstance(restored, LogMenu)
        self.assertIsNot(restored, logs)
        self.assertFalse(restored.is_finished())
        await control(restored, "Home").callback(self.interaction())
        self.assertIsInstance(self.manager.entries[1].view, SettingsView)
        self.assertEqual(self.manager.entries[1].view.section, "overview")
        self.assertIsNone(self.manager.entries[1].owner_id)

    async def test_private_logs_cannot_edit_shared_panel_via_settings_or_home(self):
        private = self.interaction(True)
        logs = LogMenu(Logs(self.bot), 50, 1, self.parent)
        await logs.show(private)
        self.assertIsNone(logs.parent.panel_manager)
        activity = next(
            item
            for item in logs.walk_children()
            if isinstance(item, discord.ui.Section)
            and item.children[0].content.startswith("**SERVER EVENTS & MEMBERS**")
        )
        event = self.interaction(True)
        await activity.accessory.callback(event)
        settings = event.edit_original_response.await_args.kwargs["view"]
        self.assertIsNone(settings.panel_manager)
        self.assertEqual(settings.section, "logging")
        home = self.interaction(True)
        await control(settings, "Home").callback(home)
        self.assertEqual(home.edit_original_response.await_args.kwargs["view"].section, "overview")
        self.message.edit.assert_not_awaited()
        self.assertFalse(self.parent.is_finished())

    async def test_shared_view_reuse_does_not_stop_callbacks_and_private_source_is_rejected(self):
        await self.manager.edit(self.parent, self.interaction(), self.parent, [])
        self.assertFalse(self.parent.is_finished())
        private = self.interaction(True)
        self.assertIsNone(await self.manager.edit(self.parent, private, self.parent, []))
        self.assertFalse(self.parent.is_finished())
        self.assertEqual(self.message.edit.await_count, 1)
        self.assertFalse(await self.manager.allowed(self.parent, private))

    async def test_private_parent_is_a_detached_owner_scoped_settings_view(self):
        detached = menu_parent(self.parent, self.interaction(True))
        self.assertIsNot(detached, self.parent)
        self.assertIsNone(detached.panel_manager)
        self.assertIsNone(detached.panel_token)
        self.assertEqual(detached.owner_id, 50)
        self.assertIs(menu_parent(self.parent, self.interaction()), self.parent)
        detached.stop()

    async def test_flash_settings_preserve_parent_through_changes_and_private_home(self):
        source = SimpleNamespace(id=10, is_nsfw=lambda: True)
        logs = SimpleNamespace(flash_config=lambda *_: None, spoiler_required=lambda *_: False)
        cog = SimpleNamespace(logs=logs, apply=AsyncMock(), show=AsyncMock(), authorize=AsyncMock())
        view = FlashMenu(cog, 50, 1, source, command_center=self.parent)
        await control(view, "Require spoilers").callback(self.interaction())
        self.assertIs(cog.show.await_args.kwargs["command_center"], self.parent)
        private = self.interaction(True)
        self.assertTrue(await view.interaction_check(private))
        await control(view, "Require spoilers").callback(private)
        self.assertIsNone(cog.show.await_args.kwargs["command_center"].panel_manager)
        await control(view, "Home").callback(self.interaction(True))
        self.message.edit.assert_not_awaited()
        view.stop()

    async def test_ordinary_roots_acknowledge_updates_without_private_responses(
        self,
    ):
        from ky_bot.admin_functions.autodelete import command as delete
        from ky_bot.admin_functions.autoreact import command as react
        from ky_bot.admin_functions.autothread import command as thread
        from ky_bot.leveling import command as leveling

        for module, kind in (
            (delete, delete.AutoDelete),
            (thread, thread.AutoThread),
            (react, react.AutoReact),
            (leveling, leveling.Leveling),
        ):
            with self.subTest(feature=module.__name__):
                session = SimpleNamespace(show=AsyncMock())
                interaction = self.interaction()
                with patch.object(module, "Session", return_value=session):
                    await kind.open_menu(SimpleNamespace(), interaction, command_center=self.parent)
                interaction.response.defer.assert_awaited_once_with()
                session.show.assert_awaited_once_with(interaction)

    async def test_settings_footers_have_no_duplicate_back_or_refresh_actions(self):
        for section in ("general", "welcome", "logging"):
            self.parent.show_section(section)
            labels = {
                item.label
                for item in self.parent.walk_children()
                if isinstance(item, discord.ui.Button)
            }
            self.assertIn("Home", labels)
            self.assertNotIn("Back", labels)
            self.assertNotIn("Refresh", labels)
        self.parent.show_section("welcome_design")
        self.assertIsNotNone(control(self.parent, "Back to Welcome"))

    async def test_checklist_log_picker_and_save_stay_on_shared_panel(self):
        from ky_bot.confessions.checklist_logs import ChecklistLogs

        boards = {20: dict(guild=1, channel=20, revision="first", log_channel=0)}

        def update(channel, **changes):
            boards[channel].update(changes, revision="second")

        store = SimpleNamespace(
            board=lambda channel: boards.get(channel), update_board=Mock(side_effect=update)
        )
        service = SimpleNamespace(
            store=store,
            channel=AsyncMock(return_value=SimpleNamespace(id=30, guild=self.guild)),
            check_permissions=Mock(),
            wakeup=Mock(),
        )
        self.bot.get_cog = lambda name: (
            SimpleNamespace(service=service) if name == "Checklists" else None
        )
        await Logs(self.bot).open_menu(self.interaction(), command_center=self.parent)
        logs = self.manager.entries[1].view
        section = next(
            item
            for item in logs.walk_children()
            if isinstance(item, discord.ui.Section)
            and item.children[0].content.startswith("**CHECKLISTS**")
        )
        event = self.interaction()
        await section.accessory.callback(event)
        view = self.manager.entries[1].view
        self.assertIsInstance(view, ChecklistLogs)
        event.response.send_message.assert_not_awaited()
        event.edit_original_response.assert_not_awaited()
        view.boards._values = [discord.Object(id=20)]
        await view.choose_board(self.interaction())
        self.assertFalse(view.logs.disabled)
        self.assertFalse(view.is_finished())
        view.logs._values = [discord.Object(id=30)]
        await view.choose_logs(self.interaction())
        store.update_board.assert_called_once_with(20, log_channel=30)
        self.assertEqual(view.revision, "second")
        self.assertFalse(view.is_finished())
        await control(view, "Back to Logs").callback(self.interaction())
        self.assertIsInstance(self.manager.entries[1].view, LogMenu)
        self.assertFalse(self.manager.entries[1].view.is_finished())

    async def test_automation_back_buttons_have_a_distinct_general_settings_destination(self):
        from ky_bot.admin_functions.autodelete import command as delete
        from ky_bot.admin_functions.autoreact import command as react
        from ky_bot.admin_functions.autothread import command as thread
        from ky_bot.design.menu_design import automation_profile

        for module, key in ((delete, "autodelete"), (thread, "autothread"), (react, "autoreact")):
            with self.subTest(feature=key):
                parent = SimpleNamespace(
                    panel_manager=SimpleNamespace(reset=AsyncMock()),
                    section="overview",
                    refresh=AsyncMock(),
                    interaction_check=AsyncMock(return_value=True),
                )
                cog = SimpleNamespace(store=SimpleNamespace(list=lambda _: []))
                interaction = self.interaction()
                session = module.Session(cog, interaction)
                session.command_center = parent
                session.design = automation_profile({}, key)
                view = module.Menu(session)
                labels = {
                    item.label
                    for item in view.walk_children()
                    if isinstance(item, discord.ui.Button)
                }
                self.assertIn("Back to General", labels)
                self.assertIn("Home", labels)
                self.assertNotIn("Back", labels)
                await control(view, "Back to General").callback(interaction)
                self.assertEqual(parent.section, "general")
                parent.refresh.assert_awaited_once_with(interaction)
                parent.panel_manager.reset.assert_not_awaited()
                view.stop()

    async def test_private_component_updates_do_not_create_another_private_response(self):
        from ky_bot.views.menu_context import defer_menu

        component = self.interaction(True)
        await defer_menu(component)
        component.response.defer.assert_awaited_once_with()
        slash = self.interaction()
        slash.message = None
        await defer_menu(slash)
        slash.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)

    async def test_standalone_private_log_menus_close_the_existing_v2_message(self):
        from ky_bot.confessions.checklist_logs import ChecklistLogs

        for view in (LogMenu(Logs(self.bot), 50, 1, None), ChecklistLogs(SimpleNamespace(), 50, 1)):
            with self.subTest(view=type(view).__name__):
                interaction = self.interaction(True)
                await view.show(interaction)
                interaction.edit_original_response.reset_mock()
                event = self.interaction(True)
                await control(view, "Close").callback(event)
                event.response.defer.assert_awaited_once_with()
                args = event.edit_original_response.await_args.kwargs
                self.assertIsNone(args["content"])
                self.assertIsInstance(args["view"], discord.ui.LayoutView)
                self.assertIn("closed", str(args["view"].to_components()))
                self.message.edit.assert_not_awaited()

    async def test_appearance_settings_are_shared_but_preview_and_its_back_are_private(self):
        from ky_bot.design.editor import DesignSession

        interaction = self.interaction()
        interaction.client = self.bot
        session = DesignSession(interaction, self.service, 0, {})
        session.command_center = self.parent
        await session.select(["settings:overview"])
        await session.show(interaction)
        shared_editor = self.manager.entries[1].view
        interaction.edit_original_response.assert_not_awaited()
        preview = self.interaction()
        preview.client = self.bot
        await control(shared_editor, "Preview").callback(preview)
        preview.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        private_view = preview.edit_original_response.await_args.kwargs["view"]
        self.assertIsNot(private_view.session, session)
        self.assertIsNone(private_view.session.command_center)
        self.assertIs(self.manager.entries[1].view, shared_editor)
        self.assertFalse(shared_editor.is_finished())
        before = self.message.edit.await_count
        private = self.interaction(True)
        private.client = self.bot
        await control(private_view, "Back to editing").callback(private)
        closed = private.edit_original_response.await_args.kwargs["view"]
        self.assertFalse(hasattr(closed, "session"))
        self.assertIn("Continue editing in the Command Center", str(closed.to_components()))
        self.assertEqual(self.message.edit.await_count, before)
        await control(shared_editor, "Cancel").callback(self.interaction())
        self.assertEqual(self.manager.entries[1].view.section, "general")

    async def test_manage_embeds_entire_workflow_stays_shared_except_preview(self):
        from ky_bot.custom_embeds.command import CustomEmbeds
        from ky_bot.custom_embeds.store import Store
        from ky_bot.custom_embeds.views import DeleteConfirm, Editor, Library, SavedEmbed

        store = await Store.open(":memory:")
        try:
            cog = CustomEmbeds(self.bot)
            cog.store = store
            target = SimpleNamespace(
                id=30, get_partial_message=lambda _: SimpleNamespace(delete=AsyncMock())
            )
            cog.channel = AsyncMock(return_value=target)
            cog.post = AsyncMock(return_value=SimpleNamespace(id=400))
            await cog.open_menu(self.interaction(), command_center=self.parent)
            library = self.manager.entries[1].view
            create = self.interaction()
            await control(library, "Create new").callback(create)
            create.response.defer.assert_awaited_once_with()
            create.edit_original_response.assert_not_awaited()
            editor = self.manager.entries[1].view
            self.assertIsInstance(editor, Editor)
            session = editor.session
            self.assertIs(session.parent, self.parent)
            await session.save(
                self.interaction(), session.row["revision"], body="Example post", channel=30
            )
            for page in (
                "main",
                "content",
                "formatting",
                "sections",
                "images",
                "style",
                "delivery",
                "channel",
                "publish",
            ):
                event = self.interaction()
                await session.show(event, page)
                self.assertIs(self.manager.entries[1].view.session, session)
                event.edit_original_response.assert_not_awaited()
                self.assertFalse(self.manager.entries[1].view.is_finished())
            editor = self.manager.entries[1].view
            before = self.message.edit.await_count
            preview = self.interaction()
            await control(editor, "Preview").callback(preview)
            self.assertTrue(cog.post.await_args.kwargs["private"])
            self.assertIs(cog.post.await_args.args[0], preview.followup)
            self.assertEqual(self.message.edit.await_count, before)
            self.assertIs(self.manager.entries[1].view, editor)
            await control(editor, "Publish").callback(self.interaction())
            self.assertIsInstance(self.manager.entries[1].view, Editor)
            self.assertTrue(session.row["published"])
            await session.show(self.interaction(), "main")
            await control(self.manager.entries[1].view, "All embeds").callback(self.interaction())
            library = self.manager.entries[1].view
            self.assertIsInstance(library, Library)
            self.assertIs(library.parent, self.parent)
            picker = next(
                item for item in library.walk_children() if isinstance(item, discord.ui.Select)
            )
            picker._values = [session.ident]
            event = self.interaction()
            await picker.callback(event)
            event.response.defer.assert_awaited_once_with()
            self.assertIsInstance(self.manager.entries[1].view, SavedEmbed)
            await control(self.manager.entries[1].view, "Edit embed").callback(self.interaction())
            self.assertIsInstance(self.manager.entries[1].view, Editor)
            await control(self.manager.entries[1].view, "All embeds").callback(self.interaction())
            await control(self.manager.entries[1].view, "Delete embed").callback(self.interaction())
            deleting = self.manager.entries[1].view
            self.assertIsInstance(deleting, Library)
            self.assertIs(deleting.parent, self.parent)
            picker = next(
                item for item in deleting.walk_children() if isinstance(item, discord.ui.Select)
            )
            picker._values = [session.ident]
            await picker.callback(self.interaction())
            self.assertIsInstance(self.manager.entries[1].view, DeleteConfirm)
            await control(self.manager.entries[1].view, "Cancel").callback(self.interaction())
            self.assertIsInstance(self.manager.entries[1].view, Editor)
            await self.manager.entries[1].view.session.show(self.interaction(), "publish")
            await control(self.manager.entries[1].view, "Delete").callback(self.interaction())
            await control(self.manager.entries[1].view, "Delete embed").callback(self.interaction())
            self.assertIsInstance(self.manager.entries[1].view, Library)
            self.assertIs(self.manager.entries[1].view.parent, self.parent)
            self.assertEqual(await store.listing(1), [])
            await control(self.manager.entries[1].view, "Home").callback(self.interaction())
            self.assertEqual(self.manager.entries[1].view.section, "overview")
        finally:
            await store.close()
