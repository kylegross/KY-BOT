from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from ky_bot.admin_functions.activity import Activity
from ky_bot.checklists.store import ChecklistStore


@pytest.mark.parametrize("title", ["MESSAGE DELETED", "MESSAGES BULK DELETED", "MESSAGE EDITED"])
@pytest.mark.parametrize("source", [10, 20, 30])
async def test_checklist_message_events_stay_out_of_server_log(title, source):
    cog, channel, _ = setup_activity(channel_id=40)
    store = ChecklistStore(":memory:")
    try:
        store.create(10, 1, 20)
        store.add(10, 123, 90, "Task", [])
        store.db.execute("UPDATE tasks SET thread=30")
        cog.bot.checklists = NS(store=store)
        await cog.emit(1, title, {}, source_channel_id=source)
        channel.send.assert_not_awaited()
        await cog.emit(1, title, {}, source_channel_id=50)
        channel.send.assert_awaited_once()
        assert not store.is_checklist_channel(2, source)
    finally:
        store.db.close()


def setup_activity(channel_id=20):
    channel = NS(send=AsyncMock())
    service = NS(
        repository=NS(get=AsyncMock(return_value=NS(log_channel_id=channel_id))),
        validate_channel=AsyncMock(return_value=channel),
    )
    bot = NS(server_settings=service, get_guild=Mock(return_value=NS(id=1)))
    return Activity(bot), channel, service


async def test_event_uses_its_guild_destination_and_disables_mentions():
    cog, channel, service = setup_activity()
    await cog.emit(1, "MEMBER JOINED", {"Member": "@everyone"})
    service.repository.get.assert_awaited_once_with(1)
    service.validate_channel.assert_awaited_once_with(cog.bot.get_guild.return_value, 20)
    options = channel.send.call_args.kwargs
    assert options["allowed_mentions"].to_dict()["parse"] == []
    assert options["embed"].title == r"\*ੈ𑁍  MEMBER JOINED  𑁍ੈ\*"


@pytest.mark.parametrize(
    "guild_id,channel_id,source", [(None, 20, None), (1, None, None), (1, 20, 20)]
)
async def test_dms_disabled_logging_and_log_channel_do_not_emit(guild_id, channel_id, source):
    cog, channel, _ = setup_activity(channel_id)
    await cog.emit(guild_id, "MESSAGE DELETED", {}, source_channel_id=source)
    channel.send.assert_not_awaited()


async def test_uncached_deleted_message_still_logs_ids():
    cog, channel, _ = setup_activity()
    await cog.on_raw_message_delete(
        NS(guild_id=1, channel_id=10, message_id=30, cached_message=None)
    )
    embed = channel.send.call_args.kwargs["embed"]
    assert embed.title == r"\*ੈ𑁍  MESSAGE DELETED  𑁍ੈ\*"
    assert "not in the bot's cache" in embed.fields[-1].value


async def test_cached_edit_contains_before_and_after_only_once():
    cog, channel, _ = setup_activity()
    before = NS(content="Old message", author=NS(id=123), attachments=[])
    payload = NS(
        guild_id=1,
        channel_id=10,
        message_id=30,
        cached_message=before,
        data={"content": "New message"},
    )
    await cog.on_raw_message_edit(payload)
    fields = {f.name: f.value for f in channel.send.call_args.kwargs["embed"].fields}
    assert fields["Before"] == "Old message" and fields["After"] == "New message"
    channel.send.assert_awaited_once()


async def test_embed_update_and_unchanged_content_do_not_log():
    cog, channel, _ = setup_activity()
    payload = NS(
        guild_id=1,
        channel_id=10,
        message_id=30,
        cached_message=NS(content="Same", attachments=[], author=NS(id=123)),
        data={"embeds": []},
    )
    await cog.on_raw_message_edit(payload)
    payload.data = {"content": "Same"}
    await cog.on_raw_message_edit(payload)
    channel.send.assert_not_awaited()


@pytest.mark.parametrize("cached", [True, False])
async def test_bot_panel_edits_do_not_log(cached):
    cog, channel, _ = setup_activity()
    cog.bot.user = NS(id=999)
    payload = NS(
        guild_id=1,
        channel_id=10,
        message_id=30,
        cached_message=NS(author=NS(id=999), content="Old", attachments=[]) if cached else None,
        data={"author": {"id": "999"}, "content": "", "attachments": [{"id": "456"}]},
    )
    await cog.on_raw_message_edit(payload)
    channel.send.assert_not_awaited()


async def test_uncached_checklist_panel_without_author_does_not_log():
    cog, channel, _ = setup_activity()
    cog.bot.checklists = NS(
        store=NS(
            board=Mock(
                return_value={
                    "guild": 1,
                    "message": 30,
                }
            )
        )
    )
    await cog.on_raw_message_edit(
        NS(
            guild_id=1,
            channel_id=10,
            message_id=30,
            cached_message=None,
            data={"content": "", "attachments": [{"id": "456"}]},
        )
    )
    channel.send.assert_not_awaited()


async def test_role_assignments_log_added_and_removed_roles():
    cog, channel, _ = setup_activity()
    before = NS(roles=[NS(id=2, name="Old")])
    after = NS(id=123, guild=NS(id=1), roles=[NS(id=3, name="New")])
    await cog.on_member_update(before, after)
    fields = {f.name: f.value for f in channel.send.call_args.kwargs["embed"].fields}
    assert "New" in fields["Roles added"] and "Old" in fields["Roles removed"]


async def test_bans_unbans_and_uncached_departure():
    cog, channel, _ = setup_activity()
    user = NS(id=123)
    await cog.on_member_ban(NS(id=1), user)
    await cog.on_member_unban(NS(id=1), user)
    await cog.on_raw_member_remove(NS(guild_id=1, user=user))
    assert [c.kwargs["embed"].title for c in channel.send.call_args_list] == [
        r"\*ੈ𑁍  MEMBER BANNED  𑁍ੈ\*",
        r"\*ੈ𑁍  MEMBER UNBANNED  𑁍ੈ\*",
        r"\*ੈ𑁍  MEMBER LEFT  𑁍ੈ\*",
    ]


async def test_invite_deletion_handles_partial_invite():
    cog, channel, _ = setup_activity()
    await cog.on_invite_delete(NS(guild=NS(id=1), channel=NS(id=10), code="example"))
    assert channel.send.call_args.kwargs["embed"].title == r"\*ੈ𑁍  INVITE DELETED  𑁍ੈ\*"


async def test_invalid_destination_does_not_break_event_listener():
    cog, channel, service = setup_activity()
    service.validate_channel.side_effect = ValueError("Missing channel")
    await cog.emit(1, "MEMBER JOINED", {})
    channel.send.assert_not_awaited()


async def test_bulk_delete_limits_cached_content_and_reports_total():
    cog, channel, _ = setup_activity()
    payload = NS(
        guild_id=1,
        channel_id=10,
        message_ids=set(range(10)),
        cached_messages=[NS(id=i, author=NS(id=123), content="x" * 2000) for i in range(10)],
    )
    await cog.on_raw_bulk_message_delete(payload)
    embed = channel.send.call_args.kwargs["embed"]
    assert embed.title == r"\*ੈ𑁍  MESSAGES BULK DELETED  𑁍ੈ\*"
    assert embed.fields[1].value == "10"
    assert all(len(field.value) <= 1024 for field in embed.fields)


async def test_invite_creation_and_role_lifecycle():
    cog, channel, _ = setup_activity()
    await cog.on_invite_create(
        NS(
            guild=NS(id=1),
            channel=NS(id=10),
            code="example",
            inviter=NS(id=123),
        )
    )
    old = NS(
        id=2,
        guild=NS(id=1),
        name="Old",
        colour=discord.Colour(0),
        permissions=discord.Permissions.none(),
        position=1,
        hoist=False,
        mentionable=False,
    )
    new = NS(**vars(old))
    new.name = "New"
    await cog.on_guild_role_create(old)
    await cog.on_guild_role_update(old, new)
    await cog.on_guild_role_delete(new)
    assert [c.kwargs["embed"].title for c in channel.send.call_args_list] == [
        r"\*ੈ𑁍  INVITE CREATED  𑁍ੈ\*",
        r"\*ੈ𑁍  ROLE CREATED  𑁍ੈ\*",
        r"\*ੈ𑁍  ROLE UPDATED  𑁍ੈ\*",
        r"\*ੈ𑁍  ROLE DELETED  𑁍ੈ\*",
    ]
