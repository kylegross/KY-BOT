import asyncio
import base64
import copy
import io
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
from PIL import Image

from ky_bot.admin_functions.activity import Activity
from ky_bot.admin_functions.settings_view import SettingsView
from ky_bot.tickets.artwork import normalize, public_files
from ky_bot.tickets.command import Tickets
from ky_bot.tickets.store import Store, defaults, effective, new_panel, new_type, validate
from ky_bot.tickets.views import Admin, Manage, PublicPanel, TicketButton, private_admin_parent

PANEL = "a" * 32
KIND = "b" * 32


def configuration():
    config = defaults()
    config["enabled"] = True
    config["panels"][PANEL] = new_panel("Support") | dict(
        types=[KIND], message=100, published_channel=200
    )
    config["types"][KIND] = new_type("Support")
    config["defaults"].update(
        support_roles=[7],
        log_channels=[10, 11],
        transcript_channels=[12, 13],
        category=9,
        cooldown=0,
    )
    return config


def member(user=5, roles=(), administrator=False):
    value = Mock(spec=discord.Member)
    value.id = user
    value.bot = False
    value.roles = [SimpleNamespace(id=key) for key in roles]
    value.guild_permissions = SimpleNamespace(administrator=administrator)
    return value


def interaction(user=None, guild=1):
    return SimpleNamespace(
        user=user or member(),
        guild_id=guild,
        guild=SimpleNamespace(id=guild),
        response=SimpleNamespace(
            is_done=Mock(return_value=False), defer=AsyncMock(), send_message=AsyncMock()
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
    )


class TicketTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = await Store.open(":memory:")
        self.config = configuration()
        self.revision = await self.store.configure(1, None, self.config)
        self.cog = Tickets(SimpleNamespace(get_guild=Mock(return_value=SimpleNamespace(id=1))))
        self.cog.store = self.store

    async def asyncTearDown(self):
        await self.store.close()

    async def reserve(self, owner=5):
        ticket = await self.store.reserve(1, owner, PANEL, KIND, [("How can we help?", "Test")])
        return await self.store.update(
            ticket, state="waiting_staff", channel=300, intro_sent=True, thread_mode=False
        )

    async def test_multiple_panels_and_destination_precedence(self):
        config = copy.deepcopy(self.config)
        other = "c" * 32
        config["panels"][other] = new_panel("Reports") | dict(types=[KIND], channel=201)
        config["panels"][PANEL]["overrides"] = dict(log_channels=[20, 21])
        self.assertEqual(effective(config, KIND, PANEL)["log_channels"], [20, 21])
        self.assertEqual(effective(config, KIND, other)["log_channels"], [10, 11])
        config["types"][KIND]["overrides"]["log_channels"] = [30]
        self.assertEqual(effective(config, KIND, PANEL)["log_channels"], [30])
        validate(config)
        await self.store.configure(1, self.revision, config)
        ticket = await self.reserve()
        self.assertEqual(ticket["settings"]["log_channels"], [30])

    async def test_stale_configuration_cannot_overwrite_newer_settings(self):
        updated = copy.deepcopy(self.config)
        updated["enabled"] = False
        await self.store.configure(1, self.revision, updated)
        with self.assertRaisesRegex(ValueError, "Settings changed"):
            await self.store.configure(1, self.revision, self.config)

    async def test_concurrent_open_limit_is_atomic(self):
        self.config["defaults"]["max_open"] = 1
        await self.store.configure(1, self.revision, self.config)
        results = await asyncio.gather(*(self.reserve() for _ in range(4)), return_exceptions=True)
        self.assertEqual(sum(isinstance(value, dict) for value in results), 1)
        self.assertEqual(sum(isinstance(value, ValueError) for value in results), 3)

    async def test_closed_tickets_release_limit_but_keep_cooldown(self):
        self.config["defaults"].update(max_open=1, cooldown=60)
        await self.store.configure(1, self.revision, self.config)
        ticket = await self.reserve()
        await self.store.update(ticket, state="closed")
        with self.assertRaisesRegex(ValueError, "wait"):
            await self.reserve()

    async def test_guild_isolation(self):
        ticket = await self.reserve()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            await self.cog.access(interaction(guild=2), ticket["id"])
        _, other = await self.store.config(2)
        self.assertEqual(other["panels"], {})

    async def test_snapshot_keeps_existing_ticket_destinations(self):
        ticket = await self.reserve()
        self.config["defaults"]["log_channels"] = [99]
        await self.store.configure(1, self.revision, self.config)
        self.assertEqual((await self.store.get(ticket["id"]))["settings"]["log_channels"], [10, 11])

    async def test_events_fan_out_without_duplicates_and_retry_survives(self):
        ticket = await self.reserve()
        ticket["settings"]["log_channels"] = [10, 10, 11]
        await self.store.event(ticket, "TEST", 5)
        events = await self.store.events()
        self.assertEqual({row[2] for row in events}, {10, 11})
        await self.store.retry(events[0][0])
        ready = await self.store.events()
        self.assertEqual(len(ready), 1)
        self.assertEqual(len(await self.store.events(ticket["id"])), 2)

    async def test_pending_archive_checks_beyond_delivery_batch(self):
        ticket = await self.reserve()
        for _ in range(60):
            await self.store.event(ticket, "TEST", 5)
        await self.store.event(ticket, "ARCHIVE", 5, archive=True)
        self.assertTrue(await self.store.pending_archives(ticket["id"]))

    async def test_transcript_failure_preserves_open_ticket(self):
        ticket = await self.reserve()
        channel = SimpleNamespace(edit=AsyncMock())
        self.cog.get_channel = AsyncMock(return_value=channel)
        self.cog.transcript = AsyncMock(side_effect=ValueError("Cannot export"))
        with self.assertRaises(ValueError):
            await self.cog.close_ticket(ticket, 5, "Done")
        channel.edit.assert_not_awaited()
        self.assertEqual((await self.store.get(ticket["id"]))["state"], "waiting_staff")

    async def test_failed_archive_delivery_prevents_deletion(self):
        ticket = await self.reserve()
        ticket = await self.store.update(ticket, state="closed")
        channel = SimpleNamespace(delete=AsyncMock())
        self.cog.get_channel = AsyncMock(return_value=channel)
        self.cog.transcript = AsyncMock(return_value="archive")
        self.cog.deliver_events = AsyncMock()
        i = interaction(member(6, roles=[7]))
        with self.assertRaisesRegex(ValueError, "preserved"):
            await self.cog.resolve(i, ticket["id"], "delete", "Done")
        channel.delete.assert_not_awaited()

    async def test_nonstaff_cannot_read_notes_or_change_ticket(self):
        ticket = await self.reserve()
        with self.assertRaisesRegex(ValueError, "permission"):
            await self.cog.change(interaction(), ticket["id"], "note", "secret")
        with self.assertRaisesRegex(ValueError, "permission"):
            await self.cog.access(interaction(member(99)), ticket["id"])

    async def test_staff_cannot_steal_another_claim(self):
        ticket = await self.reserve()
        await self.store.update(ticket, claimed=8)
        with self.assertRaisesRegex(ValueError, "already claimed"):
            await self.cog.change(interaction(member(9, roles=[7])), ticket["id"], "claim", None)

    async def test_rating_is_opener_only_and_once(self):
        ticket = await self.reserve()
        await self.store.update(ticket, state="closed")
        await self.cog.change(interaction(), ticket["id"], "rating", "5")
        self.assertEqual((await self.store.get(ticket["id"]))["rating"], 5)
        with self.assertRaisesRegex(ValueError, "already rated"):
            await self.cog.change(interaction(), ticket["id"], "rating", "1")

    async def test_old_public_panel_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "current ticket panel"):
            await self.cog.eligible(interaction(), PANEL, KIND, 101)

    async def test_role_block_applies_even_to_administrators(self):
        self.config["defaults"]["blocked_roles"] = [7]
        await self.store.configure(1, self.revision, self.config)
        with self.assertRaisesRegex(ValueError, "roles"):
            await self.cog.eligible(
                interaction(member(5, roles=[7], administrator=True)), PANEL, KIND, 100
            )

    async def test_closed_ticket_does_not_automate(self):
        ticket = await self.reserve()
        ticket = await self.store.update(ticket, state="closed")
        self.cog.close_ticket = AsyncMock()
        await self.cog.maintain_ticket(ticket)
        self.cog.close_ticket.assert_not_awaited()

    async def test_inactivity_always_warns_before_automatic_close(self):
        ticket = await self.reserve()
        ticket["settings"]["auto_close_hours"] = 1
        ticket = await self.store.update(
            ticket,
            state="waiting_user",
            last_activity=time.time() - 7200,
            settings=ticket["settings"],
        )
        channel = SimpleNamespace(send=AsyncMock())
        self.cog.get_channel = AsyncMock(return_value=channel)
        self.cog.close_ticket = AsyncMock()
        await self.cog.maintain_ticket(ticket)
        channel.send.assert_awaited_once()
        self.cog.close_ticket.assert_not_awaited()
        current = await self.store.get(ticket["id"])
        self.assertTrue(current["warning_at"])

    async def test_user_message_resets_inactivity_warning(self):
        ticket = await self.reserve()
        await self.store.update(ticket, state="waiting_user", reminded=True, warning_at=1)
        message = SimpleNamespace(
            guild=SimpleNamespace(id=1), channel=SimpleNamespace(id=300), author=member()
        )
        await self.cog.on_message(message)
        current = await self.store.get(ticket["id"])
        self.assertEqual(current["state"], "waiting_staff")
        self.assertFalse(current["reminded"])
        self.assertEqual(current["warning_at"], 0)

    async def test_transcript_escapes_html_and_excludes_staff_notes(self):
        ticket = await self.reserve()
        ticket = await self.store.update(ticket, notes=[dict(actor=1, text="PRIVATE STAFF NOTE")])
        message = SimpleNamespace(
            content="<script>alert(1)</script>",
            author="Member <x>",
            created_at=discord.utils.utcnow(),
            id=1,
            embeds=[],
            attachments=[],
        )
        message.author = SimpleNamespace(id=5, __str__=lambda: "member")

        async def history(**kwargs):
            yield message

        channel = SimpleNamespace(guild=SimpleNamespace(filesize_limit=8_000_000), history=history)
        self.cog.get_channel = AsyncMock(return_value=channel)
        self.cog.validate_destination = Mock()
        body = await self.cog.transcript(ticket)
        self.assertIn("&lt;script&gt;", body)
        self.assertNotIn("<script>", body)
        self.assertNotIn("PRIVATE STAFF NOTE", body)
        self.assertEqual(body, await self.store.archive(ticket))

    async def test_every_menu_fits_discord_component_and_text_limits(self):
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        cases = [
            ("home", None),
            ("panels", None),
            ("types", None),
            ("panel", PANEL),
            ("panel_types", PANEL),
            ("publish", PANEL),
            ("panel_logs", PANEL),
            ("type", KIND),
            ("defaults", None),
            ("type_workflow", KIND),
            ("access", KIND),
            ("destinations", KIND),
            ("automation", None),
            ("queue", None),
            ("replies", None),
        ]
        for page, selected in cases:
            with self.subTest(page=page):
                view = Admin(
                    self.cog,
                    parent,
                    self.revision,
                    self.config,
                    page=page,
                    selected=selected,
                    notice="Saved",
                )
                self.assertLessEqual(view.total_children_count, 40)
                self.assertLessEqual(view.content_length(), 4000)
                for item in view.walk_children():
                    if isinstance(item, discord.ui.Section):
                        self.assertIsInstance(item.accessory, discord.ui.Button)
                view.stop()
        ticket = await self.reserve()
        ticket["settings"]["saved_replies"] = {"Hello": "Hello"}
        for is_staff in (True, False):
            view = Manage(self.cog, ticket, 5, staff=is_staff)
            self.assertLessEqual(view.total_children_count, 40)
            view.stop()

    async def test_command_center_and_maximum_panel_fit(self):
        for shared in (False, True):
            view = SettingsView(
                1, 1, SimpleNamespace(), panel_manager=SimpleNamespace() if shared else None
            )
            self.assertLessEqual(view.total_children_count, 40)
            self.assertIn("TICKETS", str(view.to_components()))
            view.stop()
        for count in (1, 5, 6, 25):
            config = copy.deepcopy(self.config)
            config["types"] = {format(n, "032x"): new_type(f"Type {n}") for n in range(count)}
            config["panels"][PANEL]["types"] = list(config["types"])
            view = PublicPanel(config, PANEL, preview=True)
            self.assertLessEqual(view.total_children_count, 40)
            self.assertTrue(
                all(
                    item.item.disabled
                    for item in view.walk_children()
                    if isinstance(item, TicketButton)
                )
            )
            view.stop()

    async def test_reopen_restores_durable_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tickets.sqlite3"
            store = await Store.open(path)
            await store.configure(1, None, self.config)
            ticket = await store.reserve(1, 5, PANEL, KIND, [])
            await store.event(ticket, "OPENED", 5)
            await store.close()
            store = await Store.open(path)
            try:
                self.assertEqual((await store.get(ticket["id"]))["owner"], 5)
                self.assertEqual(len(await store.events()), 2)
                self.assertEqual((await store.config(1))[1]["panels"][PANEL]["message"], 100)
            finally:
                await store.close()

    async def test_shorter_type_retention_is_honored_and_clears_private_content(self):
        ticket = await self.reserve()
        ticket["settings"]["retention_days"] = 1
        ticket = await self.store.update(
            ticket,
            state="closed",
            settings=ticket["settings"],
            notes=[dict(actor=1, text="Private")],
            reason="Private reason",
        )
        await self.store.archive(ticket, "<html>Private conversation</html>")
        await self.store.execute(
            "UPDATE ticket_archives SET created=? WHERE id=?",
            (time.time() - 2 * 86400, ticket["id"]),
        )
        await self.store.prune(1, 30)
        self.assertIsNone(await self.store.archive(ticket))
        current = await self.store.get(ticket["id"])
        self.assertEqual(current["answers"], [])
        self.assertEqual(current["notes"], [])
        self.assertEqual(current["reason"], "")

    async def test_each_ticket_menu_uploads_its_branded_category_artwork(self):
        repository = SimpleNamespace(get_designs=AsyncMock(return_value=(None, {})))
        self.cog.bot.server_settings = SimpleNamespace(repository=repository)
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        for page, selected in (
            ("home", None),
            ("panel", PANEL),
            ("type", KIND),
            ("defaults", None),
            ("access", KIND),
            ("destinations", KIND),
            ("automation", None),
        ):
            view = Admin(self.cog, parent, self.revision, self.config, page=page, selected=selected)
            uploads = await self.cog.appearance(1, view)
            try:
                names = {upload.filename for upload in uploads}
                for child in view.walk_children():
                    if isinstance(child, discord.ui.MediaGallery):
                        for item in child.items:
                            if item.media.url.startswith("attachment://"):
                                self.assertIn(item.media.url.removeprefix("attachment://"), names)
                self.assertLessEqual(len(names), 10)
            finally:
                for upload in uploads:
                    upload.close()
                view.stop()

    async def test_creation_sets_private_permissions_and_staff_only_mentions(self):
        self.config["defaults"]["ping_staff"] = True
        await self.store.configure(1, self.revision, self.config)
        i = interaction()
        guild = Mock(spec=discord.Guild)
        guild.id = 1
        guild.me = member(100)
        guild.default_role = Mock(spec=discord.Role)
        support = Mock(spec=discord.Role)
        support.id = 7
        guild.get_role = Mock(return_value=support)
        guild.get_member = Mock(return_value=i.user)
        parent = Mock(spec=discord.TextChannel)
        parent.id = 200
        parent.guild = guild
        parent.permissions_for = Mock(return_value=discord.Permissions.all())
        channel = Mock(spec=discord.Thread)
        channel.id = 300
        channel.guild = guild
        channel.parent = parent
        channel.mention = "<#300>"
        channel.send = AsyncMock(return_value=SimpleNamespace(id=400))
        channel.edit = AsyncMock()
        channel.fetch_members = AsyncMock(return_value=[SimpleNamespace(id=100)])
        channel.add_user = AsyncMock()
        channel.remove_user = AsyncMock()
        parent.create_thread = AsyncMock(return_value=channel)
        guild.get_channel = Mock(return_value=parent)
        guild.create_text_channel = AsyncMock()
        guild.chunked = True
        guild.members = [i.user, member(9, roles=[7])]
        i.guild = guild
        self.cog.validate_type = AsyncMock()
        i.user.name = "test.user"
        await self.cog.create_ticket(i, PANEL, KIND, [("How can we help?", "An issue")], 100)
        self.assertEqual(channel.edit.await_args.kwargs["name"], "test-user-1")
        self.assertEqual(
            parent.create_thread.await_args.kwargs["type"], discord.ChannelType.private_thread
        )
        self.assertFalse(parent.create_thread.await_args.kwargs["invitable"])
        guild.create_text_channel.assert_not_awaited()
        self.assertEqual({call.args[0].id for call in channel.add_user.await_args_list}, {5, 9})
        mentions = channel.send.await_args_list[0].kwargs["allowed_mentions"]
        self.assertEqual(len(channel.send.await_args_list), 1)
        self.assertIn("## An issue", str(channel.send.await_args.kwargs["view"].to_components()))
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertEqual([value.id for value in mentions.roles], [7])
        ticket = await self.store.by_channel(1, 300)
        self.assertEqual(ticket["state"], "waiting_staff")
        self.assertTrue(ticket["intro_sent"])

    async def test_general_activity_logger_never_receives_private_ticket_content(self):
        ticket = await self.reserve()
        settings = SimpleNamespace(
            log_channel_id=99, member_log_channel_id=None, log_excluded_channel_ids=()
        )
        repository = SimpleNamespace(get=AsyncMock(return_value=settings))
        destination = AsyncMock()
        source = SimpleNamespace(parent_id=None, parent=None, category_id=9)
        guild = SimpleNamespace(get_channel_or_thread=Mock(return_value=source))
        bot = SimpleNamespace(
            server_settings=SimpleNamespace(repository=repository, validate_channel=destination),
            get_guild=Mock(return_value=guild),
            get_cog=lambda name: self.cog if name == "Tickets" else None,
        )
        await Activity(bot).emit(
            1, "MESSAGE EDITED", source_channel_id=300, fields={"Content": "PRIVATE"}
        )
        destination.assert_not_awaited()
        self.assertIsNotNone(ticket)

    async def test_slash_setup_current_channel_is_only_an_editable_new_panel_default(self):
        repository = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace()))
        self.cog.bot.server_settings = SimpleNamespace(repository=repository)
        i = interaction()
        i.channel = Mock(spec=discord.TextChannel)
        i.channel_id = 777
        i.response.send_modal = AsyncMock()
        parent = await private_admin_parent(self.cog, i)
        menu = Admin(self.cog, parent, self.revision, self.config, page="panels")
        menu.show = AsyncMock()
        await menu.create(i)
        modal = i.response.send_modal.await_args.args[0]
        await modal.commit(i, {"name": "New panel"})
        revision, config = await self.store.config(1)
        token = next(key for key in config["panels"] if key != PANEL)
        self.assertEqual(config["panels"][token]["channel"], 777)
        self.assertEqual(config["panels"][PANEL]["channel"], 0)
        editor = Admin(
            self.cog, parent, revision, config, page="panel", selected=token, pick="channel"
        )
        editor.show = AsyncMock()
        picker = next(
            child for child in editor.walk_children() if isinstance(child, discord.ui.ChannelSelect)
        )
        self.assertFalse(picker.disabled)
        self.assertEqual([value.id for value in picker.default_values], [777])
        await editor.set_value(i, "channel", 888)
        self.assertEqual((await self.store.config(1))[1]["panels"][token]["channel"], 888)
        menu.stop()
        editor.stop()
        parent.stop()

    async def test_command_center_new_panel_has_an_explicit_channel_picker(self):
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        i = interaction()
        i.response.send_modal = AsyncMock()
        menu = Admin(self.cog, parent, self.revision, self.config, page="panels")
        menu.show = AsyncMock()
        await menu.create(i)
        await i.response.send_modal.await_args.args[0].commit(i, {"name": "Choose channel"})
        _, config = await self.store.config(1)
        token = next(key for key in config["panels"] if key != PANEL)
        self.assertEqual(config["panels"][token]["channel"], 0)
        menu.stop()

    async def test_panel_specific_settings_are_isolated_and_type_overrides_win(self):
        config = copy.deepcopy(self.config)
        other = "c" * 32
        config["panels"][other] = new_panel("Second panel") | dict(types=[KIND])
        config["panels"][PANEL]["overrides"] = dict(
            category=55,
            support_roles=[88],
            log_channels=[66],
            cooldown=120,
            welcome="Panel-specific greeting",
        )
        await self.store.configure(1, self.revision, config)
        first = effective(config, KIND, PANEL)
        second = effective(config, KIND, other)
        self.assertEqual(
            (first["category"], first["support_roles"], first["cooldown"]), (55, [88], 120)
        )
        self.assertEqual(
            (second["category"], second["support_roles"], second["cooldown"]), (9, [7], 0)
        )
        config["types"][KIND]["overrides"]["category"] = 77
        self.assertEqual(effective(config, KIND, PANEL)["category"], 77)
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        for page in (
            "panel_settings",
            "access",
            "destinations",
            "type_workflow",
            "automation",
            "replies",
        ):
            view = Admin(self.cog, parent, None, config, page=page, selected=PANEL, notice="Saved")
            self.assertLessEqual(view.total_children_count, 40)
            self.assertEqual(view.settings["category"], 55)
            view.stop()

    async def test_invalid_combined_panel_and_type_timers_are_rejected(self):
        config = copy.deepcopy(self.config)
        config["panels"][PANEL]["overrides"]["auto_close_hours"] = 2
        config["types"][KIND]["overrides"]["reminder_hours"] = 4
        with self.assertRaisesRegex(ValueError, "before automatic"):
            validate(config)

    async def test_empty_topic_screen_has_working_starter_checkboxes_and_no_duplicate_title(self):
        config = copy.deepcopy(self.config)
        config["types"] = {}
        config["panels"][PANEL]["types"] = []
        revision = await self.store.configure(1, self.revision, config)
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        i = interaction()
        view = Admin(self.cog, parent, revision, config, page="panel_types", selected=PANEL)
        view.show = AsyncMock()
        topics = [
            child
            for child in view.panel.children
            if isinstance(child, discord.ui.Section) and child.accessory.label == "☐ Select"
        ]
        self.assertEqual(len(topics), 5)
        self.assertIn("Support", topics[0].children[0].content)
        self.assertNotIn("COMMAND CENTER · TICKETS", str(view.to_components()))
        await topics[0].accessory.callback(i)
        revision, config = await self.store.config(1)
        self.assertEqual(len(config["types"]), 1)
        token = config["panels"][PANEL]["types"][0]
        self.assertEqual(config["types"][token]["name"], "Support")
        checked = Admin(self.cog, parent, revision, config, page="panel_types", selected=PANEL)
        checked.show = AsyncMock()
        control = next(
            item.accessory
            for item in checked.panel.children
            if isinstance(item, discord.ui.Section) and item.accessory.label == "☑ Selected"
        )
        await control.callback(i)
        self.assertEqual((await self.store.config(1))[1]["panels"][PANEL]["types"], [])
        self.assertLessEqual(checked.total_children_count, 40)
        view.stop()
        checked.stop()

    async def test_topic_checklist_pages_keep_other_selections(self):
        config = copy.deepcopy(self.config)
        config["types"] = {format(n, "032x"): new_type(f"Custom {n:02d}") for n in range(30)}
        existing = format(29, "032x")
        config["panels"][PANEL]["types"] = [existing]
        revision = await self.store.configure(1, self.revision, config)
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        view = Admin(
            self.cog, parent, revision, config, page="panel_types", selected=PANEL, offset=5
        )
        view.show = AsyncMock()
        control = next(
            item.accessory
            for item in view.panel.children
            if isinstance(item, discord.ui.Section) and item.accessory.label == "☐ Select"
        )
        await control.callback(interaction())
        selected = (await self.store.config(1))[1]["panels"][PANEL]["types"]
        self.assertIn(existing, selected)
        self.assertEqual(len(selected), 2)
        self.assertEqual(view.show.await_args.kwargs["offset"], 5)
        self.assertLessEqual(view.total_children_count, 40)
        view.stop()

    async def test_header_footer_can_mix_standard_and_custom_artwork(self):
        image = Image.new("RGBA", (400, 100), (12, 34, 56, 128))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        custom = normalize(stream.getvalue(), "header")
        panel = new_panel("Artwork") | dict(
            header_mode="custom", header_custom=custom, footer_finish="gold"
        )
        files = public_files(panel)
        try:
            self.assertEqual(
                [file.filename for file in files],
                ["ticket_panel_header.png", "ticket_panel_footer.png"],
            )
            with Image.open(files[0].fp) as result:
                self.assertEqual(result.getpixel((0, 0)), (12, 34, 56, 128))
            self.assertNotEqual(files[1].fp.read(), base64.b64decode(custom))
        finally:
            for file in files:
                file.close()
        with self.assertRaises(ValueError):
            normalize(b"not an image", "header")

    async def test_public_preview_attaches_banners_and_omits_repeated_title(self):
        parent = SimpleNamespace(panel_manager=None, guild_id=1)
        menu = Admin(
            self.cog, parent, self.revision, self.config, page="appearance", selected=PANEL
        )
        i = interaction()
        await menu.preview(i)
        sent = i.followup.send.await_args.kwargs
        names = {file.filename for file in sent["files"]}
        self.assertEqual(names, {"ticket_panel_header.png", "ticket_panel_footer.png"})
        text = "\n".join(
            item.content
            for item in sent["view"].walk_children()
            if isinstance(item, discord.ui.TextDisplay)
        )
        self.assertNotIn("## Contact Staff", text)
        for page in ("appearance", "art_header", "art_footer"):
            view = Admin(
                self.cog,
                parent,
                self.revision,
                self.config,
                page=page,
                selected=PANEL,
                pick="source",
            )
            self.assertLessEqual(view.total_children_count, 40)
            view.stop()
        menu.stop()
        sent["view"].stop()

    async def test_artwork_is_attached_when_updating_an_existing_public_panel(self):
        config = copy.deepcopy(self.config)
        config["panels"][PANEL]["channel"] = 200
        revision = await self.store.configure(1, self.revision, config)
        message = SimpleNamespace(id=100)
        existing = SimpleNamespace(id=100, edit=AsyncMock(return_value=message))
        channel = SimpleNamespace(
            id=200,
            get_partial_message=Mock(return_value=existing),
            permissions_for=Mock(return_value=SimpleNamespace(attach_files=True)),
        )
        guild = SimpleNamespace(id=1, me=member(100), get_channel=Mock(return_value=channel))
        self.cog.validate_destination = Mock()
        self.cog.validate_type = AsyncMock()
        await self.cog.publish(guild, PANEL, revision)
        attachments = existing.edit.await_args.kwargs["attachments"]
        self.assertEqual(
            {file.filename for file in attachments},
            {"ticket_panel_header.png", "ticket_panel_footer.png"},
        )

    async def test_approval_role_is_required_and_normal_status_cannot_bypass_it(self):
        ticket = await self.reserve()
        ticket["settings"].update(approver_roles=[8], require_approval=True)
        ticket = await self.store.update(
            ticket, state="pending_approval", settings=ticket["settings"]
        )
        channel = SimpleNamespace(edit=AsyncMock(), send=AsyncMock())
        self.cog.get_channel = AsyncMock(return_value=channel)
        self.cog.overwrites = Mock(return_value={})
        with self.assertRaisesRegex(ValueError, "approvers"):
            await self.cog.action(interaction(member(9, roles=[7])), ticket["id"], "approve")
        channel.edit.assert_not_awaited()
        with self.assertRaisesRegex(ValueError, "Approve this ticket"):
            await self.cog.change(
                interaction(member(9, roles=[7])), ticket["id"], "state", "waiting_staff"
            )
        await self.cog.action(interaction(member(10, roles=[8])), ticket["id"], "approve")
        current = await self.store.get(ticket["id"])
        self.assertEqual(current["state"], "waiting_staff")
        self.assertEqual(current["approved_by"], 10)

    async def test_pending_approvals_count_against_open_ticket_limit(self):
        config = copy.deepcopy(self.config)
        config["defaults"]["max_open"] = 1
        await self.store.configure(1, self.revision, config)
        ticket = await self.reserve()
        await self.store.update(ticket, state="pending_approval")
        with self.assertRaisesRegex(ValueError, "limit"):
            await self.reserve()

    async def test_existing_saved_settings_receive_new_approval_defaults(self):
        legacy = copy.deepcopy(self.config)
        legacy["defaults"].pop("approver_roles")
        legacy["defaults"].pop("require_approval")
        await self.store.execute(
            "UPDATE ticket_settings SET data=? WHERE guild=?", (json.dumps(legacy), 1)
        )
        _, config = await self.store.config(1)
        self.assertEqual(config["defaults"]["approver_roles"], [])
        self.assertFalse(config["defaults"]["require_approval"])

    async def test_pending_approval_is_private_read_only_and_not_approved_by_messages(self):
        ticket = await self.reserve()
        ticket["settings"].update(approver_roles=[8], require_approval=True)
        ticket = await self.store.update(
            ticket, state="pending_approval", settings=ticket["settings"]
        )
        guild = Mock(spec=discord.Guild)
        guild.default_role = Mock(spec=discord.Role)
        guild.me = member(100)
        support = Mock(spec=discord.Role)
        reviewer = Mock(spec=discord.Role)
        guild.get_role = Mock(side_effect=lambda key: {7: support, 8: reviewer}.get(key))
        opener = member(5)
        guild.get_member = Mock(return_value=opener)
        permissions = self.cog.overwrites(guild, ticket, closed=True)
        self.assertFalse(permissions[guild.default_role].view_channel)
        self.assertTrue(permissions[reviewer].view_channel)
        self.assertFalse(permissions[opener].send_messages)
        message = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            channel=SimpleNamespace(id=300),
            author=member(9, roles=[7]),
        )
        await self.cog.on_message(message)
        self.assertEqual((await self.store.get(ticket["id"]))["state"], "pending_approval")


if __name__ == "__main__":
    unittest.main()
