import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import discord
from PIL import Image

from ky_bot.admin_functions.settings_repository import open_settings
from ky_bot.cogs.core import Core
from ky_bot.stealing.animation import convert_lottie, render_lottie
from ky_bot.stealing.assets import Asset, cdn_id, emojis, from_message, name_for
from ky_bot.stealing.command import DestinationError, Steal
from ky_bot.stealing.views import Batch, Rename
from ky_bot.views.help import GUIDES, HelpView

GUILD = 222222222222222222
SOURCE = 333333333333333333
CHANNEL = 444444444444444444
MESSAGE = 555555555555555555
E1, E2 = 123456789012345678, 123456789012345679


def moving_lottie():
    return json.dumps(
        {
            "v": "5.7.4",
            "fr": 10,
            "ip": 0,
            "op": 10,
            "w": 320,
            "h": 320,
            "nm": "Moving square",
            "ddd": 0,
            "assets": [],
            "layers": [
                {
                    "ddd": 0,
                    "ind": 1,
                    "ty": 1,
                    "nm": "Square",
                    "sr": 1,
                    "sw": 40,
                    "sh": 40,
                    "sc": "#00aabb",
                    "ks": {
                        "o": {"a": 0, "k": 100},
                        "r": {"a": 0, "k": 0},
                        "p": {
                            "a": 1,
                            "k": [
                                {
                                    "t": 0,
                                    "s": [30, 150, 0],
                                    "e": [250, 150, 0],
                                    "o": {"x": 0.333, "y": 0},
                                    "i": {"x": 0.667, "y": 1},
                                },
                                {"t": 10, "s": [250, 150, 0]},
                            ],
                        },
                        "a": {"a": 0, "k": [0, 0, 0]},
                        "s": {"a": 0, "k": [100, 100, 100]},
                    },
                    "ip": 0,
                    "op": 10,
                    "st": 0,
                    "bm": 0,
                }
            ],
        }
    ).encode()


class ExpressionCopyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "settings.sqlite3"
        self.context = open_settings(self.path)
        self.repository = await self.context.__aenter__()
        permissions = discord.Permissions(create_expressions=True)
        self.member = SimpleNamespace(id=50, guild_permissions=permissions)
        self.bot_member = SimpleNamespace(
            id=999, guild_permissions=discord.Permissions(create_expressions=True)
        )
        self.uploaded = []

        async def sticker_upload(**kwargs):
            self.uploaded.append(
                (kwargs["file"].filename, kwargs["file"].fp.read(), kwargs["name"])
            )
            return SimpleNamespace(id=701)

        self.guild = SimpleNamespace(
            id=GUILD,
            name="Destination",
            features=[],
            me=self.bot_member,
            fetch_member=AsyncMock(
                side_effect=lambda ident: self.member if ident == 50 else self.bot_member
            ),
            get_member=Mock(side_effect=lambda ident: self.member if ident == 50 else None),
            create_custom_emoji=AsyncMock(return_value=SimpleNamespace(id=700)),
            create_sticker=AsyncMock(side_effect=sticker_upload),
        )
        self.bot = SimpleNamespace(
            user=SimpleNamespace(id=999),
            guilds=[self.guild],
            get_guild=Mock(side_effect=lambda ident: self.guild if ident == GUILD else None),
            get_emoji=Mock(return_value=None),
            fetch_sticker=AsyncMock(),
            fetch_channel=AsyncMock(),
            server_settings=SimpleNamespace(repository=self.repository),
        )
        self.cog = Steal(self.bot)
        await self.repository.set_steal_destination(50, GUILD)

    async def asyncTearDown(self):
        await self.context.__aexit__(None, None, None)
        self.temp.cleanup()

    def interaction(self, *, owner=50, guild=SOURCE, channel=CHANNEL):
        return SimpleNamespace(
            user=SimpleNamespace(id=owner),
            guild_id=guild,
            channel_id=channel,
            context=SimpleNamespace(value=0),
            permissions=discord.Permissions.none(),
            response=SimpleNamespace(
                defer=AsyncMock(),
                edit_message=AsyncMock(),
                send_modal=AsyncMock(),
                is_done=lambda: True,
            ),
            followup=SimpleNamespace(send=AsyncMock()),
            edit_original_response=AsyncMock(),
        )

    def batch(self, assets=None):
        return Batch(
            self.cog,
            50,
            SOURCE,
            CHANNEL,
            GUILD,
            "Destination",
            assets or [Asset("emoji", E1, "first"), Asset("emoji", E2, "second", True)],
        )

    def animated(self, fmt="PNG"):
        out = io.BytesIO()
        frames = [Image.new("RGBA", (8, 8), color) for color in ("red", "blue")]
        frames[0].save(
            out, format=fmt, save_all=True, append_images=frames[1:], duration=100, loop=0
        )
        return out.getvalue()

    async def test_multi_emoji_parser_deduplicates_and_preserves_names(self):
        assets = emojis(f"<:first:{E1}> <a:second:{E2}> <:first:{E1}>")
        self.assertEqual(len(assets), 2)
        self.assertEqual([a.name for a in assets], ["first", "second"])
        self.assertFalse(assets[0].animated)
        self.assertTrue(assets[1].animated)
        self.assertEqual(name_for(assets[0]), "first")
        self.assertEqual(name_for(assets[0], "renamed"), "renamed")
        self.assertEqual(emojis("🙂"), [])

    async def test_message_assets_include_stickers_and_embed_text(self):
        item = SimpleNamespace(id=E2, name="Moving sticker", format=discord.StickerFormatType.apng)
        message = SimpleNamespace(
            content=f"<:first:{E1}>",
            stickers=[item],
            components=[],
            embeds=[discord.Embed(description=f"<:first:{E1}>")],
        )
        assets = from_message(message)
        self.assertEqual([a.kind for a in assets], ["emoji", "sticker"])
        self.assertEqual(assets[1].format, 2)
        self.assertEqual(assets[1].name, "Moving sticker")

    async def test_download_ids_do_not_accept_arbitrary_hosts(self):
        for value in (
            "https://example.com/emojis/123456789012345678.png",
            "https://cdn.discordapp.com.evil/emojis/123456789012345678.png",
            "http://cdn.discordapp.com/emojis/123456789012345678.png",
            "https://user:pass@cdn.discordapp.com/emojis/123456789012345678.png",
        ):
            self.assertIsNone(cdn_id(value, "emoji"))
        self.assertEqual(cdn_id(f"https://cdn.discordapp.com/emojis/{E1}.png", "emoji"), E1)
        self.assertIn("animated=true", Asset("emoji", E1, "first", True).url)
        self.assertEqual(
            Asset("sticker", E1, "gif", True, 4).url,
            f"https://media.discordapp.net/stickers/{E1}.gif",
        )

    async def test_destination_is_per_user_and_persists(self):
        await self.repository.set_steal_destination(51, SOURCE)
        await self.context.__aexit__(None, None, None)
        self.context = open_settings(self.path)
        self.repository = await self.context.__aenter__()
        self.bot.server_settings.repository = self.repository
        self.assertEqual(await self.repository.get_steal_destination(50), GUILD)
        self.assertEqual(await self.repository.get_steal_destination(51), SOURCE)
        self.assertIsNone(await self.repository.get_steal_destination(52))

    async def test_set_command_checks_permission_before_saving(self):
        self.member.guild_permissions.create_expressions = False
        interaction = self.interaction()
        await Steal.set_destination.callback(self.cog, interaction, str(SOURCE))
        self.assertEqual(await self.repository.get_steal_destination(50), GUILD)
        interaction.response.defer.assert_awaited_once()
        with self.assertRaises(DestinationError):
            await self.cog.authorize(50, GUILD)

    async def test_slash_emoji_reply_selection_is_owner_scoped_and_reuses_source_checks(self):
        source_guild = SimpleNamespace(id=SOURCE)
        self.bot.get_guild.side_effect = lambda ident: (
            self.guild if ident == GUILD else source_guild if ident == SOURCE else None
        )
        reply = SimpleNamespace(
            author=SimpleNamespace(id=50, bot=False),
            guild=source_guild,
            channel=SimpleNamespace(id=CHANNEL),
            reference=SimpleNamespace(message_id=MESSAGE, channel_id=CHANNEL),
        )

        async def receive(event, *, check, timeout):
            self.assertEqual(event, "message")
            self.assertEqual(timeout, 60)
            self.assertTrue(check(reply))
            wrong = SimpleNamespace(**vars(reply))
            wrong.author = SimpleNamespace(id=51, bot=False)
            self.assertFalse(check(wrong))
            wrong.author = reply.author
            wrong.channel = SimpleNamespace(id=CHANNEL + 1)
            self.assertFalse(check(wrong))
            wrong.channel = reply.channel
            wrong.reference = None
            self.assertFalse(check(wrong))
            return reply

        self.bot.wait_for = AsyncMock(side_effect=receive)
        self.cog.source_message = AsyncMock(
            return_value=SimpleNamespace(
                content=f"<:first:{E1}> <a:second:{E2}>", stickers=[], embeds=[], components=[]
            )
        )
        interaction = self.interaction()
        await Steal.emoji.callback(self.cog, interaction)
        self.cog.source_message.assert_awaited_once_with(
            interaction, f"https://discord.com/channels/{SOURCE}/{CHANNEL}/{MESSAGE}"
        )
        view = interaction.edit_original_response.await_args.kwargs["view"]
        self.assertIsInstance(view, Batch)
        self.assertEqual(len(view.assets), 2)
        self.assertEqual(self.cog.reply_waiters, {})
        self.guild.create_custom_emoji.assert_not_awaited()
        view.stop()

    async def test_reply_selection_timeout_and_personal_only_source_are_explicit(self):
        source_guild = SimpleNamespace(id=SOURCE)
        self.bot.get_guild.side_effect = lambda ident: (
            self.guild if ident == GUILD else source_guild if ident == SOURCE else None
        )
        self.bot.wait_for = AsyncMock(side_effect=TimeoutError)
        interaction = self.interaction()
        self.assertIsNone(await self.cog.reply_source(interaction, "emoji"))
        self.assertIn("expired", interaction.edit_original_response.await_args.kwargs["content"])
        self.assertEqual(self.cog.reply_waiters, {})
        self.bot.get_guild.side_effect = lambda ident: self.guild if ident == GUILD else None
        self.bot.wait_for.reset_mock()
        with self.assertRaisesRegex(ValueError, "Apps"):
            await self.cog.reply_source(interaction, "sticker")
        self.bot.wait_for.assert_not_awaited()

    async def test_sticker_reply_filters_kind_and_preserves_custom_name(self):
        interaction = self.interaction()
        target = SimpleNamespace()
        self.cog.reply_source = AsyncMock(return_value=target)
        self.cog.start = AsyncMock()
        assets = [Asset("emoji", E1, "first"), Asset("sticker", E2, "second")]
        with patch("ky_bot.stealing.command.from_message", return_value=assets):
            await Steal.sticker.callback(self.cog, interaction, name="Custom sticker")
        self.cog.start.assert_awaited_once_with(interaction, [assets[1]], "Custom sticker")
        for command in (Steal.emoji, Steal.sticker):
            parameter = next(
                parameter for parameter in command.parameters if parameter.name == "source"
            )
            self.assertFalse(parameter.required)

    async def test_personal_message_action_uses_payload_without_fetching_history(self):
        message = SimpleNamespace(
            content=f"<:first:{E1}> <a:second:{E2}>", stickers=[], embeds=[], components=[]
        )
        i = self.interaction()
        await self.cog.message_menu(i, message)
        view = i.edit_original_response.await_args.kwargs["view"]
        self.assertIsInstance(view, Batch)
        self.assertEqual(len(view.assets), 2)
        self.assertEqual(view.destination, GUILD)
        self.bot.fetch_channel.assert_not_awaited()
        view.stop()

    async def test_custom_name_is_visible_in_initial_picker(self):
        interaction = self.interaction()
        await self.cog.start(interaction, [Asset("emoji", E1, "original")], "custom")
        view = interaction.edit_original_response.await_args.kwargs["view"]
        select = next(item for item in view.walk_children() if isinstance(item, discord.ui.Select))
        self.assertIn("custom", select.options[0].label)
        view.stop()

    async def test_message_link_cannot_read_external_server_history(self):
        with self.assertRaises(ValueError):
            await self.cog.source_message(
                self.interaction(), f"https://discord.com/channels/{SOURCE}/{CHANNEL}/{MESSAGE}"
            )
        self.bot.fetch_channel.assert_not_awaited()

    async def test_destination_permissions_are_rechecked_after_download(self):
        async def download(asset):
            self.member.guild_permissions.create_expressions = False
            return self.animated("GIF")

        self.cog.download = AsyncMock(side_effect=download)
        with self.assertRaises(DestinationError):
            await self.cog.copy(50, GUILD, Asset("emoji", E1, "original", True))
        self.guild.create_custom_emoji.assert_not_awaited()

    async def test_changed_saved_destination_prevents_upload(self):
        self.cog.download = AsyncMock(return_value=self.animated("GIF"))
        await self.repository.set_steal_destination(50, SOURCE)
        with self.assertRaises(DestinationError):
            await self.cog.copy(50, GUILD, Asset("emoji", E1, "original", True))
        self.guild.create_custom_emoji.assert_not_awaited()

    async def test_animated_emoji_bytes_and_original_name_are_preserved(self):
        raw = self.animated("GIF")
        self.cog.download = AsyncMock(return_value=raw)
        await self.cog.copy(50, GUILD, Asset("emoji", E1, "original", True))
        args = self.guild.create_custom_emoji.await_args.kwargs
        self.assertEqual(args["name"], "original")
        self.assertEqual(args["image"], raw)
        self.assertIn("Requester 50", args["reason"])

    async def test_apng_and_gif_stickers_are_not_flattened(self):
        for fmt, kind in ((2, "PNG"), (4, "GIF")):
            raw = self.animated(kind)
            self.cog.download = AsyncMock(return_value=raw)
            await self.cog.copy(50, GUILD, Asset("sticker", E1, "Original sticker", True, fmt))
            self.assertEqual(self.uploaded[-1][1], raw)
            self.assertEqual(self.uploaded[-1][2], "Original sticker")
            with Image.open(io.BytesIO(self.uploaded[-1][1])) as image:
                self.assertTrue(image.is_animated)

    async def test_lottie_conversion_produces_real_moving_apng(self):
        result = await asyncio.to_thread(convert_lottie, moving_lottie())
        self.assertLessEqual(len(result), 512 * 1024)
        with Image.open(io.BytesIO(result)) as image:
            self.assertTrue(image.is_animated)
            self.assertGreaterEqual(image.n_frames, 2)
            first = image.convert("RGBA").tobytes()
            image.seek(image.n_frames - 1)
            self.assertNotEqual(first, image.convert("RGBA").tobytes())
            self.assertEqual(image.size, (320, 320))

    async def test_normal_server_uploads_converted_apng_not_lottie(self):
        raw = self.animated("PNG")
        self.cog.download = AsyncMock(return_value=moving_lottie())
        with patch("ky_bot.stealing.animation.convert_lottie", return_value=raw):
            await self.cog.copy(50, GUILD, Asset("sticker", E1, "Lottie", True, 3))
        self.assertEqual(self.uploaded[-1][0], "sticker.png")
        self.assertEqual(self.uploaded[-1][1], raw)

    async def test_verified_server_can_keep_original_lottie_format(self):
        self.guild.features = ["VERIFIED"]
        self.cog.download = AsyncMock(return_value=moving_lottie())
        with patch("ky_bot.stealing.animation.convert_lottie") as convert:
            await self.cog.copy(50, GUILD, Asset("sticker", E1, "Lottie", True, 3))
        convert.assert_not_called()
        self.assertEqual(self.uploaded[-1][0], "sticker.json")
        self.assertEqual(json.loads(self.uploaded[-1][1]), json.loads(moving_lottie()))

    async def test_lottie_does_not_read_external_assets_or_exceed_duration_limit(self):
        value = json.loads(moving_lottie())
        value["assets"] = [{"p": "secret.png", "u": "C:/Users/", "e": 0}]
        with self.assertRaises(ValueError):
            render_lottie(json.dumps(value).encode())
        value = json.loads(moving_lottie())
        value["op"] = 100
        with self.assertRaises(ValueError):
            render_lottie(json.dumps(value).encode())

    async def test_only_checked_assets_are_copied_and_successes_not_repeated(self):
        view = self.batch(
            [
                Asset("emoji", E1, "first"),
                Asset("emoji", E2, "second"),
                Asset("emoji", E2 + 1, "third"),
            ]
        )
        view.selected = {f"emoji:{E1}", f"emoji:{E2 + 1}"}
        self.cog.copy = AsyncMock(return_value=(700, "copied"))
        await view.copy_selected(self.interaction())
        self.assertEqual(self.cog.copy.await_count, 2)
        ids = [call.args[2].ident for call in self.cog.copy.await_args_list]
        self.assertEqual(ids, [E1, E2 + 1])
        await view.copy_selected(self.interaction())
        self.assertEqual(self.cog.copy.await_count, 2)
        view.stop()

    async def test_picker_retains_other_page_checks_and_stays_under_limit(self):
        view = self.batch([Asset("emoji", E1 + i, f"emoji_{i}") for i in range(60)])
        self.assertEqual(len(view.selected), 60)
        select = next(x for x in view.walk_children() if isinstance(x, discord.ui.Select))
        self.assertEqual(select.max_values, 25)
        select._values = [f"emoji:{E1}", f"emoji:{E1 + 1}"]
        await select.callback(self.interaction())
        self.assertEqual(len(view.selected), 37)
        for mode in ("pick", "rename"):
            for page in (0, 1, 2):
                view.page, view.mode = page, mode
                view.build()
                self.assertLessEqual(view.total_children_count, 40)
                self.assertLessEqual(view.content_length(), 4000)
                view.to_components()
        view.stop()

    async def test_rename_only_changes_chosen_asset(self):
        view = self.batch()
        modal = Rename(view, view.assets[f"emoji:{E1}"])
        modal.name._value = "custom_name"
        await modal.on_submit(self.interaction())
        self.assertEqual(view.names, {f"emoji:{E1}": "custom_name"})
        self.assertEqual(view.assets[f"emoji:{E1}"].name, "first")
        view.stop()
        modal.stop()

    async def test_other_user_or_channel_cannot_copy(self):
        view = self.batch()
        self.cog.copy = AsyncMock()
        await view.copy_selected(self.interaction(owner=51))
        await view.copy_selected(self.interaction(channel=CHANNEL + 1))
        self.cog.copy.assert_not_awaited()
        view.stop()

    async def test_partial_failure_retries_only_failed_assets(self):
        view = self.batch()
        self.cog.copy = AsyncMock(side_effect=[ValueError("Bad source"), (700, "second")])
        await view.copy_selected(self.interaction())
        self.assertEqual(view.completed, {f"emoji:{E2}"})
        self.cog.copy = AsyncMock(return_value=(701, "first"))
        await view.copy_selected(self.interaction())
        self.cog.copy.assert_awaited_once()
        self.assertEqual(self.cog.copy.await_args.args[2].ident, E1)
        view.stop()

    async def test_upload_timeout_is_not_automatically_repeated(self):
        view = self.batch()
        self.cog.copy = AsyncMock(side_effect=TimeoutError())
        await view.copy_selected(self.interaction())
        self.assertEqual(view.uncertain, {f"emoji:{E1}"})
        self.cog.copy = AsyncMock(return_value=(701, "second"))
        await view.copy_selected(self.interaction())
        self.assertEqual(self.cog.copy.await_args.args[2].ident, E2)
        view.stop()

    async def test_expression_size_limits_are_still_enforced(self):
        self.cog.download = AsyncMock(return_value=b"x" * (256 * 1024 + 1))
        with self.assertRaises(ValueError):
            await self.cog.copy(50, GUILD, Asset("emoji", E1, "original"))
        self.guild.create_custom_emoji.assert_not_awaited()

    async def test_install_contexts_and_updated_help_are_complete(self):
        self.assertTrue(Steal.steal.allowed_installs.user)
        self.assertTrue(Steal.steal.allowed_installs.guild)
        self.assertTrue(self.cog.menu.allowed_installs.user)
        self.assertTrue(Core.help_command.allowed_installs.user)
        self.assertTrue(Core.help_command.allowed_contexts.private_channel)
        self.assertEqual({c.name for c in Steal.steal.commands}, {"set", "emoji", "sticker"})
        for category in (
            "expressions",
            "moderation",
            "embeds",
            "server",
            "requests",
            "checklists",
            "leveling",
            "automation",
            "admin",
            "essentials",
        ):
            self.assertIn(category, GUIDES)
        for flags in ({}, {"administrator": True}, {"ban_members": True}):
            view = HelpView(50, SOURCE, **flags)
            self.assertLessEqual(view.total_children_count, 40)
            for page in GUIDES:
                view.show(page)
                self.assertLessEqual(view.total_children_count, 40)
                self.assertLessEqual(view.content_length(), 4000)
            view.stop()


if __name__ == "__main__":
    unittest.main()
