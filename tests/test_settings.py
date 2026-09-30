import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiosqlite
import discord
import pytest
from discord import app_commands

from ky_bot.bot import KYBot
from ky_bot.config import Settings
from ky_bot.database.settings import open_settings
from ky_bot.services.settings import SettingsService
from ky_bot.views.settings import SettingsView, settings_embed


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
            assert (await cursor.fetchone())[0] == 1


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
        select = view.children[0]
        select._values = [SimpleNamespace(id=10)]
        await select.callback(interaction)
        assert (await repo.get(2)).log_channel_id == 10
        await view.children[1].callback(interaction)
        assert (await repo.get(2)).log_channel_id is None
        view.message = SimpleNamespace(edit=AsyncMock())
        await view.on_timeout()
        assert all(child.disabled for child in view.children)
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


async def test_unavailable_channel_display():
    async with open_settings(Path(":memory:")) as repo:
        await repo.set_log_channel(2, 10)
        embed = settings_embed(await repo.get(2), request().guild)
        assert "unavailable" in embed.fields[0].value
