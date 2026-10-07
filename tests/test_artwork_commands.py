from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from ky_bot.cogs.artwork import Artwork


def request():
    return SimpleNamespace(
        user=SimpleNamespace(id=123),
        guild_id=1,
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def post(*, filename="ky_header_gold.png", owner=123, admin=False, ephemeral=False):
    return SimpleNamespace(
        attachments=[SimpleNamespace(filename=filename)],
        flags=SimpleNamespace(ephemeral=ephemeral),
        guild=SimpleNamespace(id=1),
        author=SimpleNamespace(id=999),
        interaction_metadata=SimpleNamespace(user=SimpleNamespace(id=owner)),
        channel=SimpleNamespace(
            permissions_for=Mock(return_value=SimpleNamespace(manage_messages=admin))
        ),
        delete=AsyncMock(),
    )


def cog():
    return Artwork(SimpleNamespace(user=SimpleNamespace(id=999), tree=Mock()))


async def test_existing_export_can_be_deleted_by_creator():
    message, interaction = post(), request()
    await cog().delete_artwork(interaction, message)
    message.delete.assert_awaited_once()
    assert interaction.followup.send.call_args.kwargs["ephemeral"]


async def test_other_members_cannot_delete_artwork():
    message = post(owner=456)
    await cog().delete_artwork(request(), message)
    message.delete.assert_not_awaited()


async def test_moderator_can_delete_legacy_bot_artwork():
    message = post(owner=None, admin=True)
    await cog().delete_artwork(request(), message)
    message.delete.assert_awaited_once()


async def test_unrelated_images_cannot_be_deleted():
    message = post(filename="holiday.png", admin=True)
    await cog().delete_artwork(request(), message)
    message.delete.assert_not_awaited()


async def test_private_reply_explains_dismissal():
    message, interaction = post(ephemeral=True), request()
    await cog().delete_artwork(interaction, message)
    message.delete.assert_not_awaited()
    assert "Dismiss message" in interaction.response.send_message.call_args.args[0]
