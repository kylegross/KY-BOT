from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from ky_bot.admin_functions.activity import Activity
from ky_bot.checklists.store import ChecklistStore


def invite_guild(invites):
    return NS(id=1, me=NS(guild_permissions=NS(manage_guild=True)),
              invites=AsyncMock(return_value=invites))


async def test_join_invite_logs_inferred_inviter_and_code():
    cog, channel, _ = setup_activity()
    inviter = NS(id=42)
    guild = invite_guild([NS(code="welcome", uses=2, inviter=inviter)])
    await cog.prime_invites(guild)
    guild.invites.return_value = [NS(code="welcome", uses=3, inviter=inviter)]
    await cog.on_member_join(NS(id=99, guild=guild, created_at=discord.utils.utcnow()))
    fields = {f.name: f.value for f in channel.send.call_args.kwargs["embed"].fields}
    assert fields["Invited by"] == "<@42> · User ID: `42`"
    assert "Invite code" not in fields
    assert "User ID: `99`" in fields["Member"]
    assert await cog.join_invite(guild) is None
    cog.bot.server_settings.repository.save_member_inviter.assert_awaited_once_with(1, 99, 42)


@pytest.mark.parametrize("before,after", [
    (None, [("a", 1)]),
    ([("a", 0)], [("a", 2)]),
    ([("a", 0), ("b", 0)], [("a", 1), ("b", 1)]),
    ([("a", 0)], []),
    ([], [("new", 1)]),
])
async def test_ambiguous_or_missing_invites_are_not_guessed(before, after):
    cog, _, _ = setup_activity()
    if before is not None:
        cog.invite_snapshots[1] = {code: (uses, NS(id=42)) for code, uses in before}
    guild = invite_guild([NS(code=code, uses=uses, inviter=NS(id=42)) for code, uses in after])
    assert await cog.join_invite(guild) is None


async def test_invite_permission_loss_discards_baseline_and_keeps_member_log():
    cog, channel, _ = setup_activity()
    guild = invite_guild([])
    cog.invite_snapshots[1] = {"a": (0, NS(id=42))}
    guild.me.guild_permissions.manage_guild = False
    await cog.on_member_join(NS(id=99, guild=guild, created_at=discord.utils.utcnow()))
    guild.invites.assert_not_awaited()
    assert 1 not in cog.invite_snapshots
    assert channel.send.call_args.kwargs["embed"].title == r"\*ੈ𑁍  MEMBER JOINED  𑁍ੈ\*"


async def test_invite_api_failure_and_guild_isolation():
    cog, _, _ = setup_activity()
    guild = invite_guild([])
    cog.invite_snapshots[2] = {"other": (5, NS(id=42))}
    guild.invites.side_effect = discord.Forbidden(NS(status=403, reason="Forbidden"), "Denied")
    assert await cog.join_invite(guild) is None
    assert cog.invite_snapshots[2]["other"][0] == 5


async def test_invite_events_update_cache_without_rewinding_use_count():
    cog, channel, _ = setup_activity()
    cog.invite_snapshots[1] = {"a": (3, NS(id=42))}
    invite = NS(guild=NS(id=1), channel=NS(id=10), code="a", inviter=NS(id=42), uses=0)
    await cog.on_invite_create(invite)
    assert cog.invite_snapshots[1]["a"][0] == 3
    await cog.on_invite_delete(invite)
    assert "a" not in cog.invite_snapshots[1]
    channel.send.assert_not_awaited()


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
        repository=NS(get=AsyncMock(return_value=NS(
            log_channel_id=channel_id, member_log_channel_id=channel_id)),
            save_member_inviter=AsyncMock(), get_member_inviter=AsyncMock(return_value=None)),
        validate_channel=AsyncMock(return_value=channel),
    )
    bot = NS(server_settings=service, get_guild=Mock(return_value=NS(id=1)))
    return Activity(bot), channel, service


async def test_event_uses_its_guild_destination_and_disables_mentions():
    cog, channel, service = setup_activity()
    await cog.emit(1, "MEMBER JOINED", {"Member": "@everyone"})
    service.repository.get.assert_awaited_once_with(1)
    service.validate_channel.assert_awaited_once_with(
        cog.bot.get_guild.return_value, 20, require_images=True
    )
    options = channel.send.call_args.kwargs
    assert options["allowed_mentions"].to_dict()["parse"] == []
    assert options["embed"].title == r"\*ੈ𑁍  MEMBER JOINED  𑁍ੈ\*"


@pytest.mark.parametrize(
    "title", ["MEMBER JOINED", "MEMBER LEFT"]
)
async def test_member_events_only_use_separate_destination_with_compact_card(title):
    cog, channel, service = setup_activity()
    service.repository.get.return_value = NS(log_channel_id=20, member_log_channel_id=30)
    await cog.emit(1, title, {"Member": "Example"},
                   subject=NS(display_avatar=NS(url="https://cdn.discordapp.com/avatar.png")))
    service.validate_channel.assert_awaited_once_with(
        cog.bot.get_guild.return_value, 30, require_images=True
    )
    embed = channel.send.call_args.kwargs["embed"]
    assert embed.thumbnail.url == "https://cdn.discordapp.com/avatar.png"
    assert embed.description
    assert embed.footer.text == "KY BOT"
    assert embed.footer.icon_url == "attachment://ky_bot_logo.png"
    assert channel.send.call_args.kwargs["file"].filename == "ky_bot_logo.png"
    assert channel.send.call_args.kwargs["file"].description is None


async def test_unconfigured_member_logging_does_not_fall_back_to_server_events():
    cog, channel, service = setup_activity()
    service.repository.get.return_value = NS(log_channel_id=20, member_log_channel_id=None)
    await cog.emit(1, "MEMBER JOINED", {})
    channel.send.assert_not_awaited()
    await cog.emit(1, "ROLE UPDATED", {})
    service.validate_channel.assert_awaited_once_with(cog.bot.get_guild.return_value, 20)


@pytest.mark.parametrize("inviter_id", [42, None])
async def test_departure_uses_saved_inviter_and_silent_clickable_mentions(inviter_id):
    cog, channel, service = setup_activity()
    service.repository.get_member_inviter.return_value = inviter_id
    await cog.on_raw_member_remove(NS(guild_id=1, user=NS(id=99)))
    service.repository.get_member_inviter.assert_awaited_once_with(1, 99)
    options = channel.send.call_args.kwargs
    fields = {f.name: f.value for f in options["embed"].fields}
    assert fields["Member"] == "<@99> · User ID: `99`"
    assert fields["Invited by"] == ("<@42> · User ID: `42`" if inviter_id else "Unknown")
    assert options["allowed_mentions"].to_dict()["parse"] == []


@pytest.mark.parametrize("title", ["INVITE CREATED", "INVITE DELETED"])
async def test_invite_events_cannot_be_published(title):
    cog, channel, _ = setup_activity()
    await cog.emit(1, title, {})
    channel.send.assert_not_awaited()


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
    channel.send.assert_not_awaited()


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
        r"\*ੈ𑁍  ROLE CREATED  𑁍ੈ\*",
        r"\*ੈ𑁍  ROLE UPDATED  𑁍ੈ\*",
        r"\*ੈ𑁍  ROLE DELETED  𑁍ੈ\*",
    ]
