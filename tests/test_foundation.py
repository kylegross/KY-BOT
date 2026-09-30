import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord import app_commands

from ky_bot.bot import KYBot
from ky_bot.config import ConfigurationError, Settings
from ky_bot.errors import report_error
from ky_bot.logging_setup import RedactingFormatter
from ky_bot.views.help import HelpView


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    for key in ("DISCORD_TOKEN", "COMMAND_SYNC", "DEV_GUILD_ID", "LOG_LEVEL", "LOG_DIR"):
        monkeypatch.delenv(key, raising=False)
    return tmp_path / ".env"


def test_missing_token_is_actionable(clean_env):
    with pytest.raises(ConfigurationError, match="DISCORD_TOKEN"):
        Settings.from_env(clean_env)


def test_environment_overrides_dotenv_and_token_is_hidden(clean_env, monkeypatch):
    clean_env.write_text("DISCORD_TOKEN=file-test-value\n", encoding="utf-8")
    monkeypatch.setenv("DISCORD_TOKEN", "environment-test-value")
    settings = Settings.from_env(clean_env)
    assert settings.token == "environment-test-value"
    assert settings.token not in repr(settings)


@pytest.mark.parametrize(
    "mode,guild", [("invalid", ""), ("guild", ""), ("guild", "abc"), ("guild", "-1")]
)
def test_invalid_sync_config(clean_env, monkeypatch, mode, guild):
    monkeypatch.setenv("DISCORD_TOKEN", "test-only-value")
    monkeypatch.setenv("COMMAND_SYNC", mode)
    monkeypatch.setenv("DEV_GUILD_ID", guild)
    with pytest.raises(ConfigurationError):
        Settings.from_env(clean_env)


@pytest.mark.parametrize("mode,expected_calls", [("none", 0), ("guild", 1), ("global", 1)])
async def test_extensions_intents_and_sync_scope(mode, expected_calls):
    async with KYBot(Settings("test-only-value", mode, 123456789)) as bot:
        bot.tree.sync = AsyncMock(return_value=[])
        await bot.setup_hook()
        assert {command.name for command in bot.tree.get_commands()} == {"help", "ping"}
        assert bot.intents.members and bot.intents.message_content
        assert not bot.intents.presences
        assert bot.tree.sync.await_count == expected_calls
        if mode == "guild":
            assert bot.tree.sync.call_args.kwargs["guild"].id == 123456789
        elif mode == "global":
            bot.tree.sync.assert_awaited_once_with()
        await bot.unload_extension("ky_bot.cogs.core")
        assert not bot.tree.get_commands()


def interaction(*, done=False, user_id=1):
    return SimpleNamespace(
        id=123,
        user=SimpleNamespace(id=user_id),
        response=SimpleNamespace(
            is_done=Mock(return_value=done), send_message=AsyncMock(), edit_message=AsyncMock()
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


@pytest.mark.parametrize("done", [False, True])
async def test_error_after_defer_uses_followup(done):
    request = interaction(done=done)
    await report_error(request, RuntimeError("private diagnostic"))
    sender = request.followup.send if done else request.response.send_message
    sender.assert_awaited_once()
    assert sender.call_args.kwargs["ephemeral"] is True
    assert "private diagnostic" not in sender.call_args.args[0]


async def test_permission_error_is_friendly():
    request = interaction()
    await report_error(request, app_commands.MissingPermissions(["manage_messages"]))
    assert "permissions" in request.response.send_message.call_args.args[0]


async def test_menu_owner_timeout_and_navigation():
    view = HelpView(1)
    assert await view.interaction_check(interaction())
    assert not await view.interaction_check(interaction(user_id=2))
    request = interaction()
    select = view.children[0]
    select._values = ["automation"]
    await select.callback(request)
    assert request.response.edit_message.call_args.kwargs["embed"].title == "Automation"
    view.message = SimpleNamespace(edit=AsyncMock())
    await view.on_timeout()
    assert all(child.disabled for child in view.children)
    view.message.edit.assert_awaited_once()
    await view.children[1].callback(request)
    assert view.is_finished()
    assert request.response.edit_message.call_args.kwargs["view"] is None


async def test_resources_close_with_bot():
    callback = AsyncMock()
    async with KYBot(Settings("test-only-value")) as bot:
        bot.resources.push_async_callback(callback)
    callback.assert_awaited_once()


def test_token_redacted_from_message_and_traceback():
    secret = "fake-secret-for-test"
    error = RuntimeError(secret)
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        "test",
        1,
        "Token: %s",
        (secret,),
        (type(error), error, error.__traceback__),
    )
    rendered = RedactingFormatter(secret).format(record)
    assert secret not in rendered
    assert "[REDACTED]" in rendered


async def test_help_command_sends_private_view():
    async with KYBot(Settings("test-only-value")) as bot:
        await bot.setup_hook()
        request = interaction()
        request.original_response = AsyncMock()
        command = bot.tree.get_command("help")
        await command.callback(bot.get_cog("Core"), request)
        kwargs = request.response.send_message.call_args.kwargs
        assert kwargs["ephemeral"] is True
        assert isinstance(kwargs["view"], discord.ui.View)
        kwargs["view"].stop()
