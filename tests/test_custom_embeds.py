import base64
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from PIL import Image

from ky_bot.admin_functions.settings_view import SettingsView
from ky_bot.custom_embeds.command import CustomEmbeds
from ky_bot.custom_embeds.model import color, defaults, image_upload, render, styled, validate
from ky_bot.custom_embeds.store import Store
from ky_bot.custom_embeds.views import DeleteConfirm, EditModal, Editor, Library, Session


class CustomEmbedTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "settings.sqlite3"
        self.store = await Store.open(self.path)
        self.ident = await self.store.create(1)
        self.bot = SimpleNamespace(user=SimpleNamespace(id=999))
        self.cog = CustomEmbeds(self.bot)
        self.cog.store = self.store
        self.cog.authorize = AsyncMock()
        self.messages = {}
        self.timeline = []
        self.next_id = 100
        self.deleted = []

        async def history(limit=1, after=None, oldest_first=False):
            ids = [ident for ident in self.timeline if after is None or ident > after.id]
            if not oldest_first:
                ids = list(reversed(ids))
            if limit is not None:
                ids = ids[:limit]
            for ident in ids:
                yield SimpleNamespace(id=ident, author=SimpleNamespace(id=50))

        def partial(ident):
            async def remove():
                self.deleted.append(ident)
                if ident in self.timeline:
                    self.timeline.remove(ident)

            return SimpleNamespace(delete=AsyncMock(side_effect=remove))

        self.channel = SimpleNamespace(
            id=10,
            history=Mock(side_effect=history),
            get_partial_message=Mock(side_effect=partial),
            fetch_message=AsyncMock(side_effect=lambda ident: self.messages[ident]),
        )
        self.cog.channel = AsyncMock(return_value=self.channel)

        async def post(channel, data, *, message=None, private=False):
            if message:
                return message
            self.next_id += 100
            ident = self.next_id
            self.timeline.append(ident)
            result = SimpleNamespace(id=ident, delete=partial(ident).delete)
            self.messages[ident] = result
            return result

        self.cog.post = AsyncMock(side_effect=post)

    async def asyncTearDown(self):
        await self.store.close()
        self.temp.cleanup()

    def interaction(self, *, admin=True, user=50, guild=1):
        return SimpleNamespace(
            user=SimpleNamespace(id=user),
            guild_id=guild,
            guild=SimpleNamespace(id=guild),
            permissions=SimpleNamespace(administrator=admin),
            response=SimpleNamespace(defer=AsyncMock(), is_done=lambda: True),
            followup=SimpleNamespace(send=AsyncMock()),
        )

    async def save(self, **changes):
        current = await self.store.get(1, self.ident)
        return await self.store.save(1, self.ident, current["revision"], **changes)

    async def publish(self, *, count=0):
        revision = await self.save(body="Original **message**", channel=10, sticky_every=count)
        await self.cog.publish(self.interaction(), self.ident, revision)

    async def test_content_color_and_empty_post_validation(self):
        self.assertEqual(color("#39dccb"), "39DCCB")
        self.assertEqual(color("000000"), "000000")
        for invalid in ("red", "#FFF", "#GGGGGG", "#1234567"):
            with self.assertRaises(ValueError):
                color(invalid)
        with self.assertRaises(ValueError):
            validate(defaults() | dict(channel=10), publish=True)
        with self.assertRaises(ValueError):
            validate(defaults() | dict(body="x" * 3501))
        with self.assertRaises(ValueError):
            validate(defaults() | dict(sticky_every=10001))
        data = validate(defaults() | dict(body="**Bold**\n> quote\n[link](https://example.com)"))
        self.assertIn("[link]", data["body"])

    async def test_formatting_and_special_lettering(self):
        self.assertEqual(styled("KY BOT", "special"), "𝙆𝙔 𝘽𝙊𝙏")
        self.assertEqual(styled("hello", "bold"), "**hello**")
        self.assertEqual(styled("one\ntwo", "quote"), "> one\n> two")
        self.assertEqual(styled("Welcome ♡", "special"), "𝙒𝙚𝙡𝙘𝙤𝙢𝙚 ♡")

    async def test_uploads_are_saved_as_real_png_not_expiring_urls(self):
        raw = io.BytesIO()
        Image.new("RGB", (20, 10), "teal").save(raw, format="JPEG")
        encoded = image_upload(raw.getvalue())
        self.assertTrue(base64.b64decode(encoded).startswith(b"\x89PNG\r\n\x1a\n"))
        with self.assertRaises(ValueError):
            image_upload(b"Not an image")

    async def test_render_preserves_markdown_and_orders_images(self):
        raw = io.BytesIO()
        Image.new("RGBA", (5, 5), "red").save(raw, format="PNG")
        data = defaults() | dict(
            title="Title",
            body="**Bold**\n## Heading",
            footer_text="Footer",
            color="000000",
            header=image_upload(raw.getvalue()),
            footer=image_upload(raw.getvalue()),
        )
        view, files = render(data)
        try:
            self.assertEqual(len(files), 2)
            types = [type(child) for child in view.children[0].children]
            self.assertEqual(
                types,
                [
                    discord.ui.MediaGallery,
                    discord.ui.TextDisplay,
                    discord.ui.TextDisplay,
                    discord.ui.MediaGallery,
                ],
            )
            self.assertIn("**Bold**", str(view.to_components()))
            self.assertLessEqual(view.total_children_count, 40)
        finally:
            view.stop()
            for file in files:
                file.close()

    async def test_draft_edit_does_not_change_published_snapshot(self):
        await self.publish(count=3)
        await self.save(body="New draft", sticky_every=1)
        row = await self.store.get(1, self.ident)
        self.assertEqual(row["draft"]["body"], "New draft")
        self.assertEqual(row["published"]["body"], "Original **message**")
        self.assertEqual(row["sticky_every"], 3)

    async def test_cross_server_and_stale_draft_are_rejected(self):
        old = (await self.store.get(1, self.ident))["revision"]
        await self.save(name="Changed")
        with self.assertRaises(ValueError):
            await self.store.save(1, self.ident, old, body="Overwrite")
        with self.assertRaises(ValueError):
            await self.store.get(2, self.ident)
        with self.assertRaises(ValueError):
            await self.store.save(2, self.ident, old, body="Other server")

    async def test_publish_updates_existing_message_instead_of_duplicate(self):
        await self.publish()
        revision = await self.save(body="Published update")
        await self.cog.publish(self.interaction(), self.ident, revision)
        self.assertEqual(self.timeline, [200])
        self.assertIsNotNone(self.cog.post.await_args.kwargs["message"])
        self.assertEqual(
            (await self.store.get(1, self.ident))["published"]["body"], "Published update"
        )

    async def test_repost_only_after_threshold_and_duplicate_events_do_not_count(self):
        await self.publish(count=2)
        self.timeline.append(201)
        await self.store.note(1, 10, 201)
        await self.store.note(1, 10, 201)
        await self.cog.move(1, self.ident)
        self.assertEqual(self.cog.post.await_count, 1)
        self.timeline.append(202)
        await self.store.note(1, 10, 202)
        await self.cog.move(1, self.ident)
        self.assertEqual(self.timeline, [201, 202, 300])
        self.assertEqual(self.deleted, [200])
        self.assertEqual((await self.store.get(1, self.ident))["message_count"], 0)

    async def test_own_messages_and_other_channels_do_not_count(self):
        await self.publish(count=1)
        message = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=10),
            author=SimpleNamespace(id=999),
            id=201,
        )
        await self.cog.on_message(message)
        await self.store.note(1, 11, 202)
        await self.store.note(2, 10, 203)
        self.assertEqual((await self.store.get(1, self.ident))["message_count"], 0)
        message.author.id = 50
        await self.cog.on_message(message)
        self.assertEqual((await self.store.get(1, self.ident))["message_count"], 1)

    async def test_sticky_uses_live_snapshot_not_unpublished_draft(self):
        await self.publish(count=1)
        await self.save(body="Still a draft")
        self.timeline.append(201)
        await self.store.note(1, 10, 201)
        await self.cog.move(1, self.ident)
        self.assertEqual(self.cog.post.await_args.args[1]["body"], "Original **message**")

    async def test_counts_and_content_survive_restart(self):
        await self.publish(count=3)
        await self.store.note(1, 10, 201)
        await self.store.close()
        self.store = await Store.open(self.path)
        self.cog.store = self.store
        row = await self.store.get(1, self.ident)
        self.assertEqual(row["message_count"], 1)
        self.assertEqual(row["sticky_every"], 3)
        self.assertEqual(row["message"], 200)

    async def test_restart_recovers_missed_message_count(self):
        await self.publish(count=2)
        self.timeline.extend([201, 202, 203])
        await self.cog.recover(1, self.ident)
        self.assertEqual((await self.store.get(1, self.ident))["pending"], 1)
        await self.cog.move(1, self.ident)
        self.assertEqual(self.timeline[-1], 300)
        self.assertEqual((await self.store.get(1, self.ident))["last_seen"], 300)

    async def test_old_copy_deletion_failure_is_retained_for_retry(self):
        await self.publish(count=1)
        self.timeline.append(201)
        await self.store.note(1, 10, 201)
        error = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "No access")
        self.channel.get_partial_message = Mock(
            return_value=SimpleNamespace(delete=AsyncMock(side_effect=error))
        )
        await self.cog.move(1, self.ident)
        row = await self.store.get(1, self.ident)
        self.assertEqual(row["message"], 300)
        self.assertEqual(row["retired"], [dict(channel=10, message=200)])
        with self.assertRaises(ValueError):
            await self.cog.move(1, self.ident)
        self.assertEqual(self.cog.post.await_count, 2)

    async def test_delete_removes_only_own_post_and_stops_sticky(self):
        await self.publish(count=1)
        self.timeline.append(201)
        row = await self.store.get(1, self.ident)
        await self.store.delete(1, self.ident, row["revision"])
        self.assertEqual(await self.store.targets(1, 10), [])
        self.assertTrue(await self.cog.clean(1, self.ident))
        self.assertEqual(self.timeline, [201])
        with self.assertRaises(ValueError):
            await self.store.get(1, self.ident)

    async def test_permission_removal_blocks_editor_and_fresh_publish_check(self):
        session = Session(self.cog, 50, 1, self.ident)
        self.assertFalse(await session.allowed(self.interaction(admin=False)))
        self.assertFalse(await session.allowed(self.interaction(user=51)))
        cog = CustomEmbeds(self.bot)
        guild = SimpleNamespace(
            fetch_member=AsyncMock(
                return_value=SimpleNamespace(guild_permissions=discord.Permissions.none())
            )
        )
        with self.assertRaises(ValueError):
            await cog.authorize(SimpleNamespace(guild=guild, user=SimpleNamespace(id=50)))

    async def test_standard_artwork_dropdowns_save_independent_durable_images(self):
        from ky_bot.custom_embeds.artwork import standard_image
        from ky_bot.design.menu_design import FINISHES

        original = image_upload(self.png())
        await self.save(header=original, footer=original)
        session = Session(self.cog, 50, 1, self.ident)
        for key in ("header", "footer"):
            for finish in FINISHES:
                session.row = await self.store.get(1, self.ident)
                session.page = "images"
                other = "footer" if key == "header" else "header"
                preserved = session.row["draft"][other]
                view = Editor(session)
                picker = next(
                    item
                    for item in view.walk_children()
                    if isinstance(item, discord.ui.Select)
                    and item.placeholder == f"Choose a standard {key}"
                )
                self.assertEqual([option.value for option in picker.options], list(FINISHES))
                picker._values = [finish]
                with patch.object(session, "show", new=AsyncMock()):
                    await picker.callback(self.interaction())
                saved = (await self.store.get(1, self.ident))["draft"]
                self.assertEqual(saved[key], standard_image(key, finish))
                self.assertEqual(saved[other], preserved)
                self.assertTrue(base64.b64decode(saved[key]).startswith(b"\x89PNG"))
                self.assertEqual(self.timeline, [])
                view.stop()
        # An upload can replace a preset; removing it clears the same saved image field.
        await self.save(header=original)
        session.row = await self.store.get(1, self.ident)
        view = Editor(session)
        remove = next(
            item.accessory
            for item in view.panel.children
            if isinstance(item, discord.ui.Section)
            and item.children[0].content.startswith("**REMOVE HEADER IMAGE**")
        )
        with patch.object(session, "show", new=AsyncMock()):
            await remove.callback(self.interaction())
        self.assertIsNone((await self.store.get(1, self.ident))["draft"]["header"])
        view.stop()

    async def test_formatting_exposes_styles_and_finish_without_cross_menu_hops(self):
        await self.save(text_color="#39DCCB")
        session = Session(self.cog, 50, 1, self.ident)
        session.row = await self.store.get(1, self.ident)
        session.page = "formatting"
        view = Editor(session)
        pickers = [item for item in view.walk_children() if isinstance(item, discord.ui.Select)]
        self.assertEqual(len(pickers), 3)
        self.assertEqual(len(pickers[-1].options), 5)
        pickers[-1]._values = ["gold"]
        with patch.object(session, "show", new=AsyncMock()):
            await pickers[-1].callback(self.interaction())
        saved = (await self.store.get(1, self.ident))["draft"]
        self.assertEqual(saved["text_finish"], "gold")
        self.assertIsNone(saved["text_color"])
        view.stop()

    async def test_all_editor_pages_and_modals_fit_discord_limits(self):
        await self.save(header=image_upload(self.png()), footer=image_upload(self.png()))
        session = Session(self.cog, 50, 1, self.ident)
        session.row = await self.store.get(1, self.ident)
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
            session.page = page
            view = Editor(session, "Draft saved.")
            self.assertLessEqual(view.total_children_count, 40)
            self.assertLessEqual(view.content_length(), 4000)
            view.to_components()
            if page != "main":
                backs = [
                    item
                    for item in view.walk_children()
                    if isinstance(item, discord.ui.Section)
                    and isinstance(item.accessory, discord.ui.Button)
                    and item.accessory.label == "Back"
                ]
                self.assertEqual(len(backs), 1, page)
                self.assertFalse(
                    any(
                        isinstance(item, discord.ui.ActionRow)
                        and any(
                            isinstance(button, discord.ui.Button) and button.label == "Back"
                            for button in item.children
                        )
                        for item in view.panel.children
                    ),
                    page,
                )
            view.stop()
        for mode in (
            "content",
            "title",
            "body",
            "footer_text",
            "name",
            "color",
            "count",
            "format",
            "header",
            "footer",
        ):
            modal = EditModal(session, mode)
            self.assertLessEqual(len(modal.children), 5)
            modal.stop()
        rows = [(str(i), "Saved embed", 10, 200, 0) for i in range(30)]
        for page in (0, 1):
            view = Library(self.cog, 50, 1, rows, page=page)
            self.assertLessEqual(view.total_children_count, 40)
            view.stop()
        parent = SettingsView(50, 1, Mock())
        for section, picker in (
            ("overview", False),
            ("overview", "command_center"),
            ("general", False),
        ):
            parent.show_section(section, picker=picker)
            self.assertLessEqual(parent.total_children_count, 40)
        parent.stop()

    async def test_all_manage_embed_footer_controls_are_right_and_neutral(self):
        rows = await self.store.listing(1)
        session = Session(self.cog, 50, 1, self.ident)
        session.row = await self.store.get(1, self.ident)
        from ky_bot.custom_embeds.views import SavedEmbed

        views = [
            Library(self.cog, 50, 1, rows),
            SavedEmbed(session),
            DeleteConfirm(session, session.row["revision"]),
        ]
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
            session.page = page
            views.append(Editor(session))
        for view in views:
            for item in view.walk_children():
                if isinstance(item, discord.ui.Button):
                    self.assertNotEqual(item.style, discord.ButtonStyle.danger)
                    if item.label in {"Back", "Close", "Cancel", "All embeds", "Home"}:
                        self.assertTrue(
                            any(
                                isinstance(section, discord.ui.Section)
                                and section.accessory is item
                                for section in view.panel.children
                            ),
                            item.label,
                        )
            view.stop()
        library = views[0]
        children = list(library.panel.children)
        delete = next(
            index
            for index, item in enumerate(children)
            if isinstance(item, discord.ui.Section)
            and item.children[0].content.startswith("**DELETE EMBED**")
        )
        self.assertIsInstance(children[delete - 1], discord.ui.Separator)
        self.assertFalse(children[delete - 1].visible)
        self.assertEqual(children[delete - 1].spacing, discord.SeparatorSpacing.large)

    async def test_manage_embeds_selection_exposes_edit_and_confirmed_delete(self):
        from ky_bot.custom_embeds.views import SavedEmbed, editor_artwork

        rows = await self.store.listing(1)
        view = Library(self.cog, 50, 1, rows)

        def controls(view):
            return {
                item.label: item
                for item in view.walk_children()
                if isinstance(item, discord.ui.Button)
            }

        self.assertIn("Delete embed", controls(view))
        picker = next(x for x in view.walk_children() if isinstance(x, discord.ui.Select))
        picker._values = [self.ident]
        with patch("ky_bot.custom_embeds.views.editor_display", new=AsyncMock()) as display:
            await picker.callback(self.interaction())
        selected = display.await_args.args[1]
        self.assertIsInstance(selected, SavedEmbed)
        self.assertEqual(set(controls(selected)), {"Edit embed", "Delete embed", "All embeds"})
        self.assertLessEqual(selected.total_children_count, 40)
        artwork = editor_artwork(selected)
        for item in artwork:
            item.close()
        with patch.object(selected.session, "show", new=AsyncMock()) as show:
            await controls(selected)["Edit embed"].callback(self.interaction())
        show.assert_awaited_once()
        with patch("ky_bot.custom_embeds.views.editor_display", new=AsyncMock()) as display:
            await controls(selected)["Delete embed"].callback(self.interaction())
        self.assertIsInstance(display.await_args.args[1], DeleteConfirm)
        self.assertEqual((await self.store.get(1, self.ident))["deleted"], 0)
        with patch("ky_bot.custom_embeds.views.editor_display", new=AsyncMock()) as display:
            await controls(view)["Delete embed"].callback(self.interaction())
        deleting = display.await_args.args[1]
        picker = next(x for x in deleting.walk_children() if isinstance(x, discord.ui.Select))
        self.assertIn("delete", picker.placeholder)
        picker._values = [self.ident]
        with patch("ky_bot.custom_embeds.views.editor_display", new=AsyncMock()) as display:
            await picker.callback(self.interaction())
        self.assertIsInstance(display.await_args.args[1], DeleteConfirm)
        self.assertEqual((await self.store.get(1, self.ident))["deleted"], 0)
        for menu in (view, selected, deleting, display.await_args.args[1]):
            menu.stop()

    async def test_library_picker_follows_saved_embeds_heading_and_home_is_right(self):
        from ky_bot.views.menu_strips import heading

        parent = SimpleNamespace(panel_manager=Mock())
        view = Library(self.cog, 50, 1, [("one", "Example", 10, 200, 0)], parent=parent)
        children = view.panel.children
        picker = next(
            n
            for n, item in enumerate(children)
            if isinstance(item, discord.ui.ActionRow)
            and any(isinstance(x, discord.ui.Select) for x in item.children)
        )
        self.assertIsNone(heading(children[picker - 1]))
        self.assertIn("Refresh after", children[picker - 1].children[0].content)
        self.assertFalse(
            any(heading(item) in {"CREATE EMBED", "SAVED EMBEDS"} for item in children)
        )
        home = next(
            x
            for x in view.walk_children()
            if isinstance(x, discord.ui.Section)
            and isinstance(x.accessory, discord.ui.Button)
            and x.accessory.label == "Home"
        )
        self.assertEqual(home.children[0].content, "**COMMAND CENTER**\nReturn to the main menu.")
        view.stop()

    async def test_every_embed_submenu_has_matching_silver_artwork_and_fits(self):
        from ky_bot.custom_embeds.views import DeleteConfirm, editor_artwork

        await self.save(
            header=image_upload(self.png()),
            footer=image_upload(self.png()),
            sections=[{"heading": "Example", "body": "Section text"}],
        )
        session = Session(self.cog, 50, 1, self.ident)
        session.row = await self.store.get(1, self.ident)
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
            "delete",
            "library",
        ):
            session.page = page
            if page == "delete":
                view = DeleteConfirm(session, session.row["revision"])
            elif page == "library":
                view = Library(self.cog, 50, 1, [])
            else:
                view = Editor(session, "Draft saved.")
            galleries = [x for x in view.walk_children() if isinstance(x, discord.ui.MediaGallery)]
            refs = {
                item.media.url.removeprefix("attachment://")
                for gallery in galleries
                for item in gallery.items
            }
            self.assertTrue(any(name.startswith("ky_strip_") for name in refs), page)
            attachments = editor_artwork(view)
            try:
                self.assertLessEqual(len(attachments), 10)
                self.assertTrue(refs <= {x.filename for x in attachments}, page)
                self.assertLessEqual(view.total_children_count, 40, page)
                view.to_components()
            finally:
                for attachment in attachments:
                    attachment.close()
                view.stop()

    async def test_content_styles_apply_once_and_titles_default_to_large(self):
        from ky_bot.custom_embeds.model import text_content

        data = validate(defaults() | dict(title="Welcome", body="Hello", footer_text="Notes"))
        self.assertEqual(text_content(data), "# Welcome\n\nHello")
        data = validate(
            data | dict(body_style="bold", title_style="special", footer_style="italic")
        )
        view, _ = render(data)
        text = "\n".join(
            x.content for x in view.walk_children() if isinstance(x, discord.ui.TextDisplay)
        )
        self.assertIn("𝙒𝙚𝙡𝙘𝙤𝙢𝙚", text)
        self.assertIn("**Hello**", text)
        self.assertIn("*Notes*", text)
        view.stop()
        with self.assertRaises(ValueError):
            validate(data | dict(body_style="unknown"))

    async def test_navigation_places_styles_with_content_and_saved_embeds_only_at_home(self):
        session = Session(self.cog, 50, 1, self.ident)
        session.row = await self.store.get(1, self.ident)
        for page in ("main", "content", "images", "style", "delivery", "publish"):
            session.page = page
            view = Editor(session)
            labels = [x.label for x in view.walk_children() if isinstance(x, discord.ui.Button)]
            self.assertEqual("All embeds" in labels, page == "main")
            selects = [x for x in view.walk_children() if isinstance(x, discord.ui.Select)]
            if page == "content":
                self.assertEqual(len(selects), 4)
                self.assertIn("Add styled text", labels)
                self.assertIn("Edit title", labels)
            if page == "style":
                self.assertEqual(len(selects), 1)
                self.assertNotIn("Add styled text", labels)
            view.stop()

    async def test_new_draft_keeps_initial_channel_and_channel_picker_displays_it(self):
        from ky_bot.custom_embeds.views import initial_channel

        channel = Mock(spec=discord.TextChannel)
        channel.id = 12345
        interaction = self.interaction()
        interaction.channel = channel
        interaction.edit_original_response = AsyncMock()
        self.assertEqual(initial_channel(interaction), 12345)
        await self.cog.open_editor(interaction)
        view = interaction.edit_original_response.await_args.kwargs["view"]
        self.assertEqual(view.session.row["draft"]["channel"], 12345)
        picker = next(x for x in view.walk_children() if isinstance(x, discord.ui.ChannelSelect))
        self.assertEqual(picker.default_values[0].id, 12345)
        view.stop()

    async def test_field_style_change_and_append_keep_other_content(self):
        await self.save(title="Welcome", body="First", footer_text="Footer")
        session = Session(self.cog, 50, 1, self.ident)
        session.page = "content"
        session.row = await self.store.get(1, self.ident)
        session.show = AsyncMock()
        modal = EditModal(session, "format")
        modal.fields["text"]._value = "Second"
        await modal.on_submit(self.interaction())
        row = await self.store.get(1, self.ident)
        self.assertEqual(row["draft"]["body"], "First\n\n**Second**")
        self.assertEqual(row["draft"]["title"], "Welcome")
        session.row = row
        view = Editor(session)
        title_select = next(
            x
            for x in view.walk_children()
            if isinstance(x, discord.ui.Select) and x.placeholder == "Choose title style"
        )
        title_select._values = ["italic"]
        await title_select.callback(self.interaction())
        row = await self.store.get(1, self.ident)
        self.assertEqual(row["draft"]["title_style"], "italic")
        self.assertEqual(row["draft"]["body"], "First\n\n**Second**")
        self.assertEqual(row["draft"]["footer_text"], "Footer")
        view.stop()

    def png(self):
        buffer = io.BytesIO()
        Image.new("RGBA", (4, 4), "teal").save(buffer, format="PNG")
        return buffer.getvalue()

    async def test_preview_is_private_and_mentions_are_suppressed(self):
        channel = SimpleNamespace(send=AsyncMock())
        await CustomEmbeds.post(
            self.cog, channel, defaults() | dict(body="@everyone **hello**"), private=True
        )
        args = channel.send.await_args.kwargs
        self.assertTrue(args["ephemeral"])
        self.assertFalse(args["allowed_mentions"].everyone)
        self.assertFalse(args["allowed_mentions"].roles)

    async def test_stale_publish_revision_does_not_post(self):
        row = await self.store.get(1, self.ident)
        await self.save(body="Content", channel=10)
        with self.assertRaises(ValueError):
            await self.cog.publish(self.interaction(), self.ident, row["revision"])
        self.cog.post.assert_not_awaited()

    async def test_cancelled_delete_keeps_draft_and_published_message(self):
        await self.publish()
        session = Session(self.cog, 50, 1, self.ident)
        session.row = await self.store.get(1, self.ident)
        view = DeleteConfirm(session, session.row["revision"])
        cancel = next(
            x
            for x in view.walk_children()
            if isinstance(x, discord.ui.Button) and x.label == "Cancel"
        )
        with patch.object(session, "show", new=AsyncMock()):
            await cancel.callback(self.interaction())
        self.assertEqual((await self.store.get(1, self.ident))["deleted"], 0)
        self.assertEqual(self.timeline, [200])
        view.stop()


if __name__ == "__main__":
    unittest.main()

    async def test_manage_embeds_has_create_edit_context_and_compact_plain_navigation(self):
        from ky_bot.custom_embeds.views import editor_artwork

        for creating in (True, False):
            session = Session(self.cog, 50, 1, self.ident, creating=creating)
            session.row = await self.store.get(1, self.ident)
            for page in ("main", "content", "images", "delivery", "publish"):
                session.page = page
                view = Editor(session)
                self.assertEqual(view.header_mode, "create" if creating else "edit")
                attachments = editor_artwork(view)
                try:
                    self.assertEqual(attachments[0].filename, "ky_header_create_embed.png")
                    self.assertTrue(attachments[0].fp.read().startswith(b"\x89PNG"))
                finally:
                    for attachment in attachments:
                        attachment.close()
                children = list(view.panel.children)
                if page == "main":
                    nav = next(
                        item
                        for item in children
                        if isinstance(item, discord.ui.Section)
                        and item.accessory.label == "All embeds"
                    )
                    self.assertEqual(nav.children[0].content.split("\n")[0], "**SAVED EMBEDS**")
                    self.assertIsInstance(children[children.index(nav) - 1], discord.ui.Separator)
                    channel_row = next(
                        item
                        for item in children
                        if isinstance(item, discord.ui.ActionRow)
                        and isinstance(item.children[0], discord.ui.ChannelSelect)
                    )
                    self.assertIsInstance(
                        children[children.index(channel_row) - 1], discord.ui.TextDisplay
                    )
                view.stop()
