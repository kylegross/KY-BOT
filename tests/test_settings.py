import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiosqlite
import discord
import pytest
from discord import app_commands

from ky_bot.admin_functions.settings_command import ServerSettings
from ky_bot.admin_functions.settings_repository import open_settings
from ky_bot.admin_functions.settings_service import SettingsService
from ky_bot.admin_functions.settings_view import SettingsView, channel_label
from ky_bot.bot import KYBot
from ky_bot.config import Settings


async def test_persistence_isolation_clear_and_schema(tmp_path):
    path = tmp_path / "nested" / "settings.sqlite3"
    async with open_settings(path) as repo:
        assert (await repo.get(1)).log_channel_id is None
        await asyncio.gather(repo.set_log_channel(1, 10), repo.set_log_channel(2, 20))
        await repo.set_log_channel(1, 11)
    async with open_settings(path) as repo:
        assert (await repo.get(1)).log_channel_id == 11
        assert (await repo.get(2)).log_channel_id == 20
        await repo.clear_log_channel(1)
        assert (await repo.get(1)).log_channel_id is None
        assert (await repo.get(2)).log_channel_id == 20
        async with repo.connection.execute("PRAGMA user_version") as cursor:
            assert (await cursor.fetchone())[0] == 3


async def test_newer_schema_rejected(tmp_path):
    path = tmp_path / "newer.sqlite3"
    async with aiosqlite.connect(path) as connection:
        await connection.execute("PRAGMA user_version = 99")
    with pytest.raises(RuntimeError, match="newer"):
        async with open_settings(path):
            pytest.fail("A newer schema must not open")


def request(*, admin=True, user=1, guild_id=2):
    return SimpleNamespace(
        id=5,
        user=SimpleNamespace(id=user),
        guild_id=guild_id,
        guild=SimpleNamespace(id=guild_id, get_channel=Mock(return_value=None)),
        permissions=discord.Permissions(administrator=admin),
        response=SimpleNamespace(
            is_done=Mock(return_value=False),
            send_message=AsyncMock(),
            defer=AsyncMock(),
            edit_message=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        original_response=AsyncMock(),
    )


@pytest.mark.parametrize(
    "admin,user,guild_id,allowed",
    [
        (True, 1, 2, True),
        (False, 1, 2, False),
        (True, 99, 2, False),
        (True, 1, 99, False),
        (True, 1, None, False),
    ],
)
async def test_component_authorization(admin, user, guild_id, allowed):
    view = SettingsView(1, 2, Mock())
    assert (
        await view.interaction_check(request(admin=admin, user=user, guild_id=guild_id)) == allowed
    )
    view.stop()


async def test_permissions_rechecked_after_menu_open():
    view = SettingsView(1, 2, Mock())
    interaction = request()
    assert await view.interaction_check(interaction)
    interaction.permissions = discord.Permissions.none()
    assert not await view.interaction_check(interaction)
    view.stop()


@pytest.mark.parametrize("missing", ["view_channel", "send_messages", "embed_links"])
async def test_channel_permissions_reject_without_writing(missing):
    repo = Mock(set_log_channel=AsyncMock())
    guild = Mock(id=2)
    channel = Mock(spec=discord.TextChannel, id=10, guild=guild)
    permissions = discord.Permissions(view_channel=True, send_messages=True, embed_links=True)
    setattr(permissions, missing, False)
    channel.permissions_for.return_value = permissions
    guild.get_channel.return_value = channel
    with pytest.raises(ValueError, match="need"):
        await SettingsService(repo).set_log_channel(guild, 10)
    repo.set_log_channel.assert_not_awaited()


async def test_channel_validation_and_valid_save():
    repo = Mock(set_log_channel=AsyncMock())
    service = SettingsService(repo)
    guild = Mock(id=2)
    guild.get_channel.return_value = None
    guild.fetch_channel = AsyncMock(return_value=Mock(spec=discord.VoiceChannel))
    with pytest.raises(ValueError, match="existing text channel"):
        await service.set_log_channel(guild, 10)
    channel = Mock(spec=discord.TextChannel, id=10, guild=SimpleNamespace(id=99))
    guild.get_channel.return_value = channel
    with pytest.raises(ValueError):
        await service.set_log_channel(guild, 10)
    repo.set_log_channel.assert_not_awaited()
    channel.guild = guild
    channel.permissions_for.return_value = discord.Permissions.all()
    await service.set_log_channel(guild, 10)
    repo.set_log_channel.assert_awaited_once_with(2, 10)


async def test_menu_saves_clears_and_disables_selector():
    async with open_settings(Path(":memory:")) as repo:
        service = SettingsService(repo)
        interaction = request()
        channel = Mock(spec=discord.TextChannel, id=10, guild=interaction.guild)
        channel.permissions_for.return_value = discord.Permissions.all()
        interaction.guild.me = Mock()
        interaction.guild.get_channel.return_value = channel
        view = SettingsView(1, 2, service)
        view.show_section("logging", picker=True)
        select = view.log_channel
        select._values = [SimpleNamespace(id=10)]
        await select.callback(interaction)
        assert (await repo.get(2)).log_channel_id == 10
        await view.clear_channel.callback(interaction)
        assert (await repo.get(2)).log_channel_id is None
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        assert all(
            child.disabled
            for child in view.walk_children()
            if isinstance(child, (discord.ui.Button, discord.ui.ChannelSelect))
        )
        view.stop()


async def test_settings_command_admin_check_and_private_response():
    async with KYBot(Settings("test", database_path=Path(":memory:"))) as bot:
        await bot.setup_hook()
        command = bot.tree.get_command("settings")
        assert command.guild_only and command.default_permissions.administrator
        with pytest.raises(app_commands.MissingPermissions):
            for check in command.checks:
                await check(request(admin=False))
        interaction = request()
        await command.callback(bot.get_cog("ServerSettings"), interaction)
        interaction.response.defer.assert_awaited_once_with(ephemeral=True, thinking=True)
        view = interaction.edit_original_response.call_args.kwargs["view"]
        assert isinstance(view, SettingsView)
        view.stop()
        connection = bot.server_settings.repository.connection
    with pytest.raises(ValueError):
        await connection.execute("SELECT 1")


async def test_uncached_channel_display():
    async with open_settings(Path(":memory:")) as repo:
        await repo.set_log_channel(2, 10)
        label = channel_label((await repo.get(2)).log_channel_id, request().guild)
        assert "reselect to verify access" in label
        assert "<#10>" in label


async def test_uncached_text_channel_is_fetched_and_saved():
    repo = Mock(set_log_channel=AsyncMock())
    guild = Mock(id=2)
    channel = Mock(spec=discord.TextChannel, id=10, guild=guild)
    channel.permissions_for.return_value = discord.Permissions.all()
    guild.get_channel.return_value = None
    guild.fetch_channel = AsyncMock(return_value=channel)
    await SettingsService(repo).set_log_channel(guild, 10)
    guild.fetch_channel.assert_awaited_once_with(10)
    repo.set_log_channel.assert_awaited_once_with(2, 10)


@pytest.mark.parametrize(
    "error_type,status,message",
    [
        (discord.NotFound, 404, "no longer exists"),
        (discord.Forbidden, 403, "View Channel"),
    ],
)
async def test_fetch_failure_is_actionable_and_does_not_save(error_type, status, message):
    repo = Mock(set_log_channel=AsyncMock())
    guild = Mock(id=2)
    guild.get_channel.return_value = None
    response = SimpleNamespace(status=status, reason="test")
    guild.fetch_channel = AsyncMock(side_effect=error_type(response, "test"))
    with pytest.raises(ValueError, match=message):
        await SettingsService(repo).set_log_channel(guild, 10)
    repo.set_log_channel.assert_not_awaited()


async def test_fetched_channel_still_requires_bot_permissions():
    repo = Mock(set_log_channel=AsyncMock())
    guild = Mock(id=2)
    channel = Mock(spec=discord.TextChannel, id=10, guild=guild)
    channel.permissions_for.return_value = discord.Permissions.none()
    guild.get_channel.return_value = None
    guild.fetch_channel = AsyncMock(return_value=channel)
    with pytest.raises(ValueError, match="need"):
        await SettingsService(repo).set_log_channel(guild, 10)
    repo.set_log_channel.assert_not_awaited()


async def test_fetched_channel_from_another_server_is_rejected():
    repo = Mock(set_log_channel=AsyncMock())
    guild = Mock(id=2)
    guild.get_channel.return_value = None
    channel = Mock(spec=discord.TextChannel, id=10, guild=SimpleNamespace(id=99))
    guild.fetch_channel = AsyncMock(return_value=channel)
    with pytest.raises(ValueError, match="this server"):
        await SettingsService(repo).set_log_channel(guild, 10)
    repo.set_log_channel.assert_not_awaited()


async def test_welcome_persistence_isolation_and_disable(tmp_path):
    path = tmp_path / "welcome.sqlite3"
    async with open_settings(path) as repo:
        await repo.set_welcome_channel(1, 10)
        await repo.set_welcome_message(1, "Hello {member}, welcome to {server}!")
        await repo.set_log_channel(1, 11)
        await repo.clear_log_channel(1)
    async with open_settings(path) as repo:
        settings = await repo.get(1)
        assert settings.welcome_channel_id == 10
        assert settings.welcome_message == "Hello {member}, welcome to {server}!"
        assert (await repo.get(2)).welcome_channel_id is None
        await repo.set_welcome_channel(1, None)
        assert (await repo.get(1)).welcome_message == settings.welcome_message


async def test_v1_upgrade_preserves_logging(tmp_path):
    path = tmp_path / "v1.sqlite3"
    async with aiosqlite.connect(path) as connection:
        await connection.executescript(
            "CREATE TABLE guild_settings (guild_id TEXT PRIMARY KEY, log_channel_id TEXT NOT NULL);"
            "INSERT INTO guild_settings VALUES ('1', '10'); PRAGMA user_version = 1;"
        )
    async with open_settings(path) as repo:
        assert (await repo.get(1)).log_channel_id == 10
        await repo.set_welcome_channel(1, 20)
        assert (await repo.get(1)).welcome_channel_id == 20


async def test_welcome_delivery_limits_mentions_and_honors_disabled():
    async with open_settings(Path(":memory:")) as repo:
        guild = Mock(id=2)
        guild.name = "Test server"
        channel = Mock(spec=discord.TextChannel, id=10, guild=guild, send=AsyncMock())
        channel.permissions_for.return_value = discord.Permissions.all()
        guild.get_channel.return_value = channel
        member = Mock(spec=discord.Member, id=123, guild=guild, mention="<@123>")
        member.display_name = "New member"
        member.display_avatar.with_format.return_value.with_size.return_value.read = AsyncMock(
            return_value=None
        )
        cog = ServerSettings(SimpleNamespace(server_settings=SettingsService(repo)))
        await cog.on_member_join(member)
        channel.send.assert_not_awaited()
        await repo.set_welcome_channel(2, 10)
        await repo.set_welcome_message(2, "Hi {member} in {server}! @everyone {other}")
        await cog.on_member_join(member)
        assert channel.send.call_args.args[0] == "Hi <@123> in Test server! @everyone {other}"
        mentions = channel.send.call_args.kwargs["allowed_mentions"]
        assert mentions.users == [member] and not mentions.everyone and not mentions.roles
        await repo.set_welcome_channel(2, None)
        await cog.on_member_join(member)
        assert channel.send.await_count == 1


async def test_overview_uses_buttons_and_channel_picker_is_on_demand():
    view = SettingsView(1, 2, Mock())
    assert isinstance(view, discord.ui.LayoutView)
    panel = view.children[0]
    assert isinstance(panel.children[0], discord.ui.MediaGallery)
    sections = [item for item in panel.children if isinstance(item, discord.ui.Section)]
    assert len(sections) == 2
    assert all(isinstance(section.accessory, discord.ui.Button) for section in sections)
    assert not any(isinstance(item, discord.ui.ChannelSelect) for item in view.walk_children())
    assert view.to_components()[0]["type"] == 17
    payload_sections = [item for item in view.to_components()[0]["components"] if item["type"] == 9]
    assert all(section["accessory"]["type"] == 2 for section in payload_sections)
    view.show_section("welcome")
    assert not any(isinstance(item, discord.ui.ChannelSelect) for item in view.walk_children())
    view.show_section("welcome", picker=True)
    assert view.welcome_channel in list(view.walk_children())
    view.stop()


async def test_inline_navigation_refresh_and_close_use_v2_payloads():
    async with open_settings(Path(":memory:")) as repo:
        view = SettingsView(1, 2, SettingsService(repo))
        interaction = request()
        welcome = next(
            item for item in view.walk_children() if isinstance(item, discord.ui.Section)
        )
        await welcome.accessory.callback(interaction)
        assert view.section == "welcome"
        kwargs = interaction.edit_original_response.call_args.kwargs
        assert "content" not in kwargs and "embed" not in kwargs
        view = kwargs["view"]
        assert view.content_length() <= 4000
        choose = next(
            item
            for item in view.walk_children()
            if isinstance(item, discord.ui.Button) and item.label == "Choose channel"
        )
        await choose.callback(interaction)
        view = interaction.edit_original_response.call_args.kwargs["view"]
        assert view.picker
        assert view.welcome_channel in list(view.walk_children())
        await view.close_menu(interaction)
        assert view.is_finished()
        assert isinstance(view.children[0], discord.ui.TextDisplay)
        interaction.response.edit_message.assert_awaited_once_with(view=view, attachments=[])


async def test_submenu_title_changes_keep_header_footer_and_caps_sections():
    from importlib.resources import files

    from ky_bot.admin_functions.settings_service import SETTINGS_TITLES

    async with open_settings(Path(":memory:")) as repo:
        view = SettingsView(1, 2, SettingsService(repo))
        interaction = request()
        uploaded = {}

        async def accept_update(**kwargs):
            uploaded.clear()
            uploaded.update({file.filename: file.fp.read() for file in kwargs["attachments"]})
            return SimpleNamespace(attachments=[])

        interaction.edit_original_response.side_effect = accept_update
        view.message = SimpleNamespace(attachments=[])
        for page in ("welcome", "logging", "welcome_design", "overview"):
            view.show_section(page)
            await view.refresh(interaction)
            view = interaction.edit_original_response.call_args.kwargs["view"]
            attachments = interaction.edit_original_response.call_args.kwargs["attachments"]
            assert len(attachments) == 3
            assert [file.filename for file in attachments[:2]] == [
                "ky_settings_header.png",
                "ky_settings_footer.png",
            ]
            assert uploaded["ky_settings_header.png"]
            assert uploaded["ky_settings_footer.png"]
            title_file = attachments[2]
            assert title_file.filename == "ky_settings_title.png"
            assert (
                uploaded[title_file.filename]
                == files("ky_bot.design")
                .joinpath("assets", "header", SETTINGS_TITLES[page][1])
                .read_bytes()
            )
            title_file.close()
            sections = [
                item for item in view.children[0].children if isinstance(item, discord.ui.Section)
            ]
            for section in sections:
                heading = section.children[0].content.split("**")[1]
                assert heading == heading.upper()
        view.stop()


async def test_failed_page_update_preserves_live_buttons_for_retry():
    from discord.ui.view import ViewStore

    async with open_settings(Path(":memory:")) as repo:
        view = SettingsView(1, 2, SettingsService(repo))
        store = ViewStore(Mock())
        store.add_view(view, 42)
        interaction = request()
        button = next(
            item
            for item in view.walk_children()
            if isinstance(item, discord.ui.Button) and item.label == "Manage"
        )
        interaction.edit_original_response.side_effect = discord.HTTPException(
            SimpleNamespace(status=400, reason="Bad Request"),
            {"code": 50035, "message": "Attachment update rejected"},
        )
        with pytest.raises(discord.HTTPException):
            await button.callback(interaction)
        assert button.view is view
        assert not view.is_finished()
        assert store._views[42][(2, button.custom_id)] is button

        async def register_update(**kwargs):
            store.add_view(kwargs["view"], 42)
            return SimpleNamespace(attachments=[])

        interaction.edit_original_response.side_effect = register_update
        await button.callback(interaction)
        replacement = interaction.edit_original_response.call_args.kwargs["view"]
        assert replacement.section == "welcome"
        assert not replacement.is_finished()
        assert view.is_finished()
        assert all(item.view is replacement for item in store._views[42].values())
        replacement.stop()


async def test_welcome_modal_saves_and_rechecks_permissions():
    from ky_bot.admin_functions.settings_view import WelcomeModal

    async with open_settings(Path(":memory:")) as repo:
        view = SettingsView(1, 2, SettingsService(repo))
        view.show_section("welcome")
        modal = WelcomeModal(view, "Welcome!")
        assert not await modal.interaction_check(request(admin=False))
        modal.message_input._value = "Hello {member}!"
        interaction = request()
        assert await modal.interaction_check(interaction)
        await modal.on_submit(interaction)
        assert (await repo.get(2)).welcome_message == "Hello {member}!"
        view.stop()
