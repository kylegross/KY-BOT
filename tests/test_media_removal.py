import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch

import discord
from navigation_audit import assert_footer_dividers

from ky_bot.custom_embeds.views import Editor, Session
from ky_bot.media_removal.command import Confirmation, MediaRemoval, Status, has_media
from ky_bot.media_removal.store import Store
from ky_bot.media_removal.views import Admin, PanelCog, render_panel


def attachment(name="photo.jpg", mime="image/jpeg"):
    return NS(filename=name, content_type=mime)


def message(ident, owner=7, attachments=None):
    return NS(id=ident, author=NS(id=owner), attachments=attachments or [], delete=AsyncMock())


def event(owner=7):
    return NS(
        user=NS(id=owner, bot=False),
        guild_id=1,
        id=1000,
        channel_id=10,
        guild=NS(id=1, me=NS(), fetch_member=AsyncMock(return_value=NS(id=owner, bot=False))),
        permissions=discord.Permissions(administrator=True),
        response=NS(
            defer=AsyncMock(),
            send_message=AsyncMock(),
            send_modal=AsyncMock(),
            edit_message=AsyncMock(),
            is_done=lambda: False,
        ),
        followup=NS(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        message=NS(id=55, author=NS(id=9)),
    )


class MediaRemovalTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "media.sqlite3"
        self.store = await Store.open(self.path)
        self.bot = NS(
            user=NS(id=9),
            get_cog=lambda _: None,
            get_channel=lambda _: None,
            fetch_channel=AsyncMock(),
        )
        self.cog = MediaRemoval(self.bot)
        self.cog.store = self.store
        self.revision, self.config = await self.store.config(1)

    async def asyncTearDown(self):
        await self.store.close()
        self.directory.cleanup()

    async def enable(self):
        self.revision, self.config = await self.store.configure(
            1, self.revision, channel=10, enabled=True, message=55, published_channel=10
        )

    async def job(self):
        await self.enable()
        job = await self.store.request(1, 7, 1000, self.revision)
        return await self.store.claim(job["id"], "worker")

    def channel(self, messages, *, allowed=True):
        async def history(**kwargs):
            for item in list(messages):
                if item.id < kwargs["before"].id:
                    yield item

        value = NS(
            id=10,
            guild=NS(id=1),
            history=history,
            permissions_for=lambda _: NS(
                view_channel=allowed, read_message_history=allowed, manage_messages=allowed
            ),
        )
        self.bot.get_channel = lambda _: value
        return value

    async def test_default_is_inactive_and_channel_is_required(self):
        self.assertFalse(self.config["enabled"])
        self.assertEqual(self.config["channel"], 0)
        with self.assertRaisesRegex(ValueError, "Choose the panel channel"):
            await self.store.configure(1, self.revision, enabled=True)
        with self.assertRaises(ValueError):
            await self.store.request(1, 7, 1000, self.revision)
        self.assertEqual(await self.store.active(), [])

    async def test_media_classifier_preserves_non_media_and_gif_only_posts(self):
        for name, mime in [
            ("a.gif", "image/gif"),
            ("a.gifv", "video/webm"),
            ("a.mp3", "audio/mpeg"),
            ("a.mp4", "audio/mp4"),
            ("a.pdf", "application/pdf"),
        ]:
            self.assertFalse(has_media(message(1, attachments=[attachment(name, mime)])))
        for name, mime in [
            ("a.jpg", "image/jpeg"),
            ("a.mov", "video/quicktime"),
            ("a.PNG", None),
            ("a.webm", "application/octet-stream"),
        ]:
            self.assertTrue(has_media(message(1, attachments=[attachment(name, mime)])))
        self.assertFalse(has_media(message(1)))
        self.assertTrue(
            has_media(message(1, attachments=[attachment("a.gif", "image/gif"), attachment()]))
        )

    async def test_requests_are_deduplicated_and_owned(self):
        job = await self.job()
        repeated = await self.store.request(1, 7, 1200, self.revision)
        self.assertEqual(repeated["id"], job["id"])
        self.assertEqual(repeated["cutoff"], 1000)
        with self.assertRaises(ValueError):
            await self.store.job(job["id"], guild=1, owner=8)
        with self.assertRaises(ValueError):
            await self.store.job(job["id"], guild=2, owner=7)
        self.assertIsNone(await self.store.claim(job["id"], "other-worker"))
        await self.store.release(job["id"], "worker")
        self.assertIsNotNone(await self.store.claim(job["id"], "other-worker"))

    async def test_only_owners_media_before_cutoff_is_removed(self):
        job = await self.job()
        own = message(100, attachments=[attachment()])
        other = message(101, owner=8, attachments=[attachment()])
        text = message(102)
        later = message(1100, attachments=[attachment()])
        gif = message(103, attachments=[attachment("a.gif", "image/gif")])
        self.channel([own, other, text, later, gif])
        await self.cog.scan(job, NS(me=NS()), 10, 1000)
        own.delete.assert_awaited_once()
        for item in (other, text, later, gif):
            item.delete.assert_not_awaited()
        saved = await self.store.job(job["id"])
        self.assertEqual(saved["deleted"], 1)
        self.assertEqual(saved["scanned"], 4)
        self.assertIsNone(await self.store.next_channel(job["id"]))

    async def test_page_cursor_survives_a_failed_delete_and_restart(self):
        job = await self.job()
        await self.store.add_channel(job, 10)
        first, second = (
            message(200, attachments=[attachment()]),
            message(100, attachments=[attachment()]),
        )
        messages = [first, second]

        async def remove_first():
            messages.remove(first)

        first.delete.side_effect = remove_first
        second.delete.side_effect = OSError("retry")
        self.channel(messages)
        with self.assertRaises(OSError):
            await self.cog.scan(job, NS(me=NS()), 10, 1000)
        self.assertEqual(await self.store.next_channel(job["id"]), (10, 1000))
        await self.store.release(job["id"], "worker")
        await self.store.close()
        self.store = await Store.open(self.path)
        self.cog.store = self.store
        resumed = await self.store.claim(job["id"], "new-worker")
        second.delete.side_effect = None
        await self.cog.scan(resumed, NS(me=NS()), 10, 1000)
        self.assertEqual((await self.store.job(job["id"]))["deleted"], 2)
        self.assertIsNone(await self.store.next_channel(job["id"]))

    async def test_disabling_pauses_work_and_prevents_further_deletions(self):
        job = await self.job()
        item = message(100, attachments=[attachment()])
        self.channel([item])
        await self.store.configure(1, self.revision, enabled=False)
        await self.cog.scan(job, NS(me=NS()), 10, 1000)
        item.delete.assert_not_awaited()
        self.assertEqual((await self.store.job(job["id"]))["state"], "paused")
        self.assertEqual(await self.store.active(), [])

    async def test_permission_failures_are_partial_not_complete(self):
        job = await self.job()
        await self.store.add_channel(job, 10)
        self.channel([], allowed=False)
        await self.cog.scan(job, NS(me=NS()), 10, 1000)
        await self.store.state(job, "running")
        saved = await self.store.job(job["id"])
        await self.cog.step(saved, NS(me=NS()))
        saved = await self.store.job(job["id"])
        self.assertEqual(saved["state"], "partial")
        status = Status(self.cog, saved)
        self.assertIn("Some media could not be removed", str(status.to_components()))
        assert_footer_dividers(self, status)
        status.stop()

    async def test_panel_click_does_not_start_deletion_before_private_confirmation(self):
        await self.enable()
        i = event()
        with patch(
            "ky_bot.media_removal.command.destination",
            return_value=NS(permissions_for=lambda _: NS(view_channel=True)),
        ):
            await self.cog.request(i)
        args = i.followup.send.await_args.kwargs
        self.assertTrue(args["ephemeral"])
        view = args["view"]
        self.assertIsInstance(view, Confirmation)
        self.assertEqual(await self.store.active(), [])
        confirm = next(
            item
            for item in view.walk_children()
            if isinstance(item, discord.ui.Button) and item.label == "Delete my media"
        )
        with patch("ky_bot.media_removal.command.send_private", new=AsyncMock()):
            await confirm.callback(event(8))
        self.assertEqual(await self.store.active(), [])
        await confirm.callback(i)
        job = await self.store.latest(1, 7)
        self.assertEqual(job["owner"], 7)
        await confirm.callback(i)
        self.assertEqual(len(await self.store.active()), 1)
        view.stop()

    async def test_stale_panel_and_stale_confirmation_are_rejected(self):
        await self.enable()
        i = event()
        i.message.id = 999
        with self.assertRaisesRegex(ValueError, "disabled or has moved"):
            await self.cog.request(i)
        view = Confirmation(self.cog, event(), self.revision)
        await self.store.configure(1, self.revision, channel=11)
        confirm = next(
            item
            for item in view.walk_children()
            if isinstance(item, discord.ui.Button) and item.label == "Delete my media"
        )
        with self.assertRaisesRegex(ValueError, "changed or is disabled"):
            await confirm.callback(event())
        self.assertEqual(await self.store.active(), [])
        view.stop()

    async def test_inline_and_below_button_previews_are_disabled_and_customizable(self):
        for inline in (True, False):
            view, files = render_panel(1, self.config | {"inline": inline}, preview=True)
            try:
                actions = [
                    item
                    for item in view.walk_children()
                    if hasattr(item, "guild") and hasattr(item, "item")
                ]
                self.assertEqual(len(actions), 1)
                self.assertTrue(actions[0].item.disabled)
                view.to_components()
                self.assertLessEqual(view.total_children_count, 40)
            finally:
                for file in files:
                    file.close()
                view.stop()

    async def test_complete_appearance_editor_is_scoped_to_this_feature(self):
        await self.enable()
        session = Session(PanelCog(self.cog, 1), 7, 1, "1", return_to=AsyncMock())
        session.row = await session.cog.store.get(1, "1")
        for page in (
            "main",
            "content",
            "formatting",
            "images",
            "header_image",
            "footer_image",
            "sections",
            "publish",
        ):
            session.page = page
            view = Editor(session)
            assert_footer_dividers(self, view)
            self.assertLessEqual(view.total_children_count, 40)
            labels = [
                item.label for item in view.walk_children() if isinstance(item, discord.ui.Button)
            ]
            self.assertNotIn("All embeds", labels)
            if page == "publish":
                self.assertNotIn("Delete", labels)
            view.stop()
        adapter = session.cog.store
        with self.assertRaises(ValueError):
            await adapter.save(1, "1", self.revision, channel=999)
        with self.assertRaises(ValueError):
            await adapter.get(2, "1")

    async def test_command_center_menu_never_publishes_on_open(self):
        i = event()
        menu = Admin(self.cog, 7, 1)
        with patch("ky_bot.media_removal.views.display_menu", new=AsyncMock()):
            await menu.show(i)
        assert_footer_dividers(self, menu)
        self.assertLessEqual(menu.total_children_count, 40)
        i.followup.send.assert_not_awaited()
        self.assertIsNone(await self.store.latest(1, 7))
        menu.stop()

    async def test_cancel_never_creates_a_job(self):
        await self.enable()
        view = Confirmation(self.cog, event(), self.revision)
        cancel = next(
            item
            for item in view.walk_children()
            if isinstance(item, discord.ui.Button) and item.label == "Cancel"
        )
        await cancel.callback(event())
        self.assertEqual(await self.store.active(), [])
        self.assertIsNone(await self.store.latest(1, 7))
        view.stop()

    async def test_thread_starters_are_kept_to_protect_other_members_replies(self):
        job = await self.job()
        await self.store.add_channel(job, 100)
        starter = message(100, attachments=[attachment()])
        reply = message(110, attachments=[attachment()])
        other = message(120, owner=8, attachments=[attachment()])

        async def history(**kwargs):
            for item in (starter, reply, other):
                yield item

        channel = Mock(spec=discord.Thread)
        channel.id, channel.guild, channel.history = 100, NS(id=1), history
        channel.permissions_for = lambda _: NS(
            view_channel=True, read_message_history=True, manage_messages=True
        )
        self.bot.get_channel = lambda _: channel
        await self.cog.scan(job, NS(me=NS()), 100, 1000)
        starter.delete.assert_not_awaited()
        reply.delete.assert_awaited_once()
        other.delete.assert_not_awaited()
        self.assertEqual(await self.store.issues(job["id"]), 1)

    async def test_discovery_includes_active_and_archived_public_and_private_threads(self):
        job = await self.job()
        calls = []

        async def archived(**kwargs):
            calls.append(kwargs)
            yield NS(id=21 if kwargs.get("private") else 20)

        channel = Mock(spec=discord.TextChannel)
        channel.id, channel.type = 10, discord.ChannelType.text
        channel.archived_threads = archived
        channel.permissions_for = lambda _: NS(
            view_channel=True, read_message_history=True, manage_threads=True
        )
        guild = NS(
            me=NS(),
            fetch_channels=AsyncMock(return_value=[channel]),
            active_threads=AsyncMock(return_value=[NS(id=30)]),
        )
        await self.cog.discover(job, guild)
        rows = await self.store.execute(
            "SELECT channel,cursor FROM media_removal_channels WHERE job=? ORDER BY channel",
            (job["id"],),
            many=True,
        )
        self.assertEqual(rows, [(10, 1000), (20, 1000), (21, 1000), (30, 1000)])
        self.assertTrue(any(call.get("private") for call in calls))
        self.assertEqual((await self.store.job(job["id"]))["state"], "running")

    async def test_moving_the_panel_pauses_active_cleanup(self):
        job = await self.job()
        await self.store.configure(1, self.revision, channel=11)
        self.assertEqual((await self.store.job(job["id"]))["state"], "paused")
        self.assertFalse(await self.store.touch(job["id"], "worker"))

    async def test_internal_deletion_receipts_are_reported_to_activity_logs(self):
        job = await self.job()
        item = message(100, attachments=[attachment()])
        self.channel([item])
        logs = NS(begin_deletion=Mock(), finish_deletion=Mock())
        self.bot.get_cog = lambda _: NS(service=NS(logs=logs))
        await self.cog.scan(job, NS(me=NS()), 10, 1000)
        logs.begin_deletion.assert_called_once_with(item)
        logs.finish_deletion.assert_called_once_with(item, True)

    async def test_approved_copy_has_decorative_title_and_editable_footer_text(self):
        self.assertEqual(self.config["panel"]["header_title"], "YOUR MEDIA, YOUR CHOICE")
        self.assertEqual(self.config["panel"]["title"], "YOUR MEDIA, YOUR CHOICE")
        self.assertEqual(self.config["panel"]["title_style"], "decorative")
        for heading in ("NEED A FRESH START?", "WHAT GETS REMOVED?", "WHAT STAYS?"):
            self.assertIn("**" + heading + "**", self.config["panel"]["body"])
        self.assertEqual(self.config["panel"]["footer_image_text"], "Your content. Your choice.")
        self.assertEqual(self.config["button_label"], "Review my media cleanup")
        adapter = PanelCog(self.cog, 1).store
        revision = await adapter.save(
            1,
            "1",
            self.revision,
            title="A CUSTOM MAIN TITLE",
            body="**CUSTOM HEADING**\nCustom paragraph.",
            header_title="Custom banner",
            footer_image_text="Custom footer",
        )
        revision, config = await self.store.configure(
            1,
            revision,
            button_label="My button",
            button_heading="My row heading",
            button_text="My row text",
        )
        reopened = await Store.open(self.path)
        try:
            _, saved = await reopened.config(1)
            self.assertEqual(saved["panel"]["title"], "A CUSTOM MAIN TITLE")
            self.assertEqual(saved["panel"]["body"], "**CUSTOM HEADING**\nCustom paragraph.")
            self.assertEqual(saved["panel"]["header_title"], "Custom banner")
            self.assertEqual(saved["panel"]["footer_image_text"], "Custom footer")
            self.assertEqual(saved["button_label"], "My button")
        finally:
            await reopened.close()

    async def test_placeholder_upgrade_preserves_custom_text_and_blank_choices(self):
        from ky_bot.media_removal.store import upgrade_copy

        old = {
            "panel": {
                "header_title": "MEDIA REMOVAL",
                "title": "REMOVE YOUR MEDIA",
                "body": "Remove your own uploaded images and videos from this server. "
                "You will confirm privately before anything is deleted.",
                "footer_image_text": "",
            },
            "button_label": "Remove my media",
            "button_heading": "YOUR MEDIA, YOUR CHOICE",
            "button_text": "Open your private cleanup options.",
        }
        upgraded = upgrade_copy(old)
        self.assertIn("NEED A FRESH START?", upgraded["panel"]["body"])
        self.assertEqual(upgraded["button_label"], "Review my media cleanup")
        old["panel"]["body"] = "My existing text"
        self.assertEqual(upgrade_copy(old)["panel"]["body"], "My existing text")
        upgraded["panel"]["footer_image_text"] = ""
        self.assertEqual(upgrade_copy(upgraded)["panel"]["footer_image_text"], "")

    def admin_event(self, *, authorized=True):
        i = event()
        actor = NS(id=7, bot=False, guild_permissions=discord.Permissions(administrator=authorized))
        target = NS(id=111222333444555666, bot=False, display_name="Target member")

        async def fetch(ident):
            return actor if ident == 7 else target

        i.guild.fetch_member = AsyncMock(side_effect=fetch)
        return i, actor, target

    async def test_admin_command_confirms_target_and_records_requester(self):
        await self.enable()
        i, actor, target = self.admin_event()
        await self.cog.admin_request(i, str(target.id))
        self.assertEqual(await self.store.active(), [])
        self.assertTrue(i.followup.send.await_args.kwargs["ephemeral"])
        view = i.followup.send.await_args.kwargs["view"]
        self.assertIn(str(target.id), str(view.to_components()))
        confirm = next(
            item
            for item in view.walk_children()
            if isinstance(item, discord.ui.Button) and item.label == "Delete member’s media"
        )
        await confirm.callback(i)
        job = await self.store.latest(1, target.id)
        self.assertEqual(job["owner"], target.id)
        self.assertIsNone(await self.store.latest(1, actor.id))
        records = await self.store.execute(
            "SELECT actor FROM media_removal_requests WHERE job=?", (job["id"],), many=True
        )
        self.assertEqual(records, [(actor.id,)])
        status = i.edit_original_response.await_args.kwargs["view"]
        self.assertEqual(status.owner, actor.id)
        self.assertEqual(status.target, target.id)
        self.assertTrue(MediaRemoval.remove_media.default_permissions.administrator)
        claimed = await self.store.claim(job["id"], "admin-worker")
        own = message(100, owner=target.id, attachments=[attachment()])
        admins = message(101, owner=actor.id, attachments=[attachment()])
        self.channel([own, admins])
        await self.cog.scan(claimed, NS(me=NS()), 10, 1000)
        own.delete.assert_awaited_once()
        admins.delete.assert_not_awaited()
        view.stop()
        status.stop()

    async def test_admin_permission_is_rechecked_at_confirmation(self):
        await self.enable()
        i, actor, target = self.admin_event()
        await self.cog.admin_request(i, f"<@{target.id}>")
        view = i.followup.send.await_args.kwargs["view"]
        actor.guild_permissions = discord.Permissions.none()
        confirm = next(
            item
            for item in view.walk_children()
            if isinstance(item, discord.ui.Button) and item.label == "Delete member’s media"
        )
        with patch("ky_bot.media_removal.command.send_private", new=AsyncMock()):
            await confirm.callback(i)
        self.assertEqual(await self.store.active(), [])
        view.stop()

    async def test_admin_command_rejects_nonadmins_and_disabled_feature(self):
        i, _, target = self.admin_event(authorized=False)
        with self.assertRaisesRegex(ValueError, "Only server administrators"):
            await self.cog.admin_request(i, str(target.id))
        i, _, target = self.admin_event()
        with self.assertRaisesRegex(ValueError, "Select a channel"):
            await self.cog.admin_request(i, str(target.id))
        self.assertEqual(await self.store.active(), [])

    async def test_action_section_has_spacing_without_a_visible_line(self):
        view, files = render_panel(1, self.config, preview=True)
        try:
            children = list(view.children[0].children)
            index = next(
                n for n, item in enumerate(children) if isinstance(item, discord.ui.Section)
            )
            self.assertIsInstance(children[index - 1], discord.ui.MediaGallery)
            self.assertIn("media_button_heading.png", str(children[index - 1].to_component_dict()))
            self.assertIsInstance(children[index - 2], discord.ui.Separator)
            self.assertFalse(children[index - 2].visible)
            self.assertEqual(children[index - 2].spacing, discord.SeparatorSpacing.small)
        finally:
            for file in files:
                file.close()
            view.stop()

    async def test_custom_image_color_controls_stay_with_their_image(self):
        session = Session(PanelCog(self.cog, 1), 7, 1, "1", return_to=AsyncMock())
        session.row = await session.cog.store.get(1, "1")
        session.page = "images"
        view = Editor(session)
        rows = [
            item.children[0].content if isinstance(item, discord.ui.Section) else item.content
            for item in view.panel.children
            if isinstance(item, (discord.ui.Section, discord.ui.TextDisplay))
        ]
        header = next(n for n, text in enumerate(rows) if "**CUSTOM HEADER COLORS**" in text)
        footer_file = next(n for n, text in enumerate(rows) if "**FOOTER FILE**" in text)
        footer = next(n for n, text in enumerate(rows) if "**CUSTOM FOOTER COLORS**" in text)
        image_text = next(n for n, text in enumerate(rows) if "**TEXT ON HEADER & FOOTER**" in text)
        self.assertLess(header, footer_file)
        self.assertLess(footer_file, footer)
        self.assertLess(footer, image_text)
        view.stop()

    async def test_direct_title_color_editor_saves_each_color_independently(self):
        from ky_bot.media_removal.views import Form

        menu = Admin(self.cog, 7, 1)
        menu.revision, menu.config = await self.store.config(1)
        menu.interaction_check = AsyncMock(return_value=True)
        menu.show = AsyncMock()
        form = Form(menu, colors=True)
        values = {
            "text_color": "#AA1188",
            "line_color": "00AA88",
            "header_text_color": "#223344",
            "footer_text_color": "#557799",
        }
        for key, value in values.items():
            form.fields[key]._value = value
        await form.on_submit(event())
        _, saved = await self.store.config(1)
        for key, value in values.items():
            self.assertEqual(saved["panel"][key], "#" + value.removeprefix("#"))
        menu.show.assert_awaited_once()
        self.assertEqual(menu.show.await_args.args[1], "Saved.")
        form.stop()
        menu.stop()

    async def test_decorative_main_title_keeps_metallic_shading_with_custom_color(self):
        import io

        from PIL import Image

        config = self.config | {"panel": self.config["panel"] | {"text_color": "#335CCC"}}
        view, files = render_panel(1, config, preview=True)
        try:
            title = next(file for file in files if file.filename == "custom_title.png")
            with Image.open(io.BytesIO(title.fp.getvalue())) as image:
                colors = {
                    pixel[:3]
                    for pixel in image.convert("RGBA").get_flattened_data()
                    if pixel[3] > 240
                }
            self.assertGreater(len(colors), 15)
        finally:
            for file in files:
                file.close()
            view.stop()

    async def test_button_heading_is_editable_decorative_metallic_and_has_a_rule(self):
        import io

        from PIL import Image

        config = self.config | {
            "button_heading": "MY CUSTOM HEADING",
            "panel": self.config["panel"] | {"text_color": "#335CCC"},
        }
        view, files = render_panel(1, config, preview=True)
        try:
            file = next(file for file in files if file.filename == "media_button_heading.png")
            with Image.open(io.BytesIO(file.fp.getvalue())).convert("RGBA") as image:
                self.assertGreater(
                    len({p[:3] for p in image.get_flattened_data() if p[3] > 240}), 15
                )
                self.assertGreater(image.getpixel((image.width - 10, image.height // 2))[3], 0)
            section = next(
                item for item in view.walk_children() if isinstance(item, discord.ui.Section)
            )
            self.assertNotIn("MY CUSTOM HEADING", section.children[0].content)
            self.assertEqual(section.accessory.item.label, config["button_label"])
        finally:
            for file in files:
                file.close()
            view.stop()
