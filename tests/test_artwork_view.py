from types import SimpleNamespace
from unittest.mock import AsyncMock

from ky_bot.views.artwork import ArtworkView


async def test_creator_can_delete_generated_message():
    view = ArtworkView(123)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=123),
        response=SimpleNamespace(defer=AsyncMock()),
        delete_original_response=AsyncMock(),
    )
    assert await view.interaction_check(interaction)
    await view.children[0].callback(interaction)
    interaction.response.defer.assert_awaited_once()
    interaction.delete_original_response.assert_awaited_once()
    assert view.is_finished()


async def test_other_user_cannot_delete_generated_message():
    view = ArtworkView(123)
    interaction = SimpleNamespace(
        user=SimpleNamespace(id=456),
        response=SimpleNamespace(send_message=AsyncMock()),
        delete_original_response=AsyncMock(),
    )
    assert not await view.interaction_check(interaction)
    interaction.delete_original_response.assert_not_awaited()
    assert interaction.response.send_message.call_args.kwargs["ephemeral"]
    view.stop()
