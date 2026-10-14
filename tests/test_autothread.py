import datetime
import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord
import pytest

from ky_bot.admin_functions.autothread.command import Menu, Session
from ky_bot.admin_functions.autothread.service import AutothreadService, qualifies, thread_name
from ky_bot.admin_functions.autothread.store import RuleStore, defaults, validate
from ky_bot.views.private_style import private_heading


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    if request.param == "postgres":
        url = os.getenv("TEST_DATABASE_URL")
        if not url:
            pytest.skip("Dedicated PostgreSQL test database not configured.")
        from ky_bot.admin_functions.autothread.postgres import PostgresRuleStore

        result = PostgresRuleStore(url)
        result.db.connection.execute(
            "TRUNCATE autothread_rules,autothread_processed,autothread_notices,"
            "autothread_retired_rules,autothread_welcome_deliveries"
        )
    else:
        result = RuleStore(tmp_path / "autothread.sqlite3")
    yield result
    result.db.close()


def configuration(content=("text",)):
    return dict(defaults(), content=list(content))


def test_durable_rules_no_defaults_and_guild_isolation(store):
    assert store.list(1) == []
    store.save_configuration(1, [10, 20], configuration())
    rule = store.list(1)[0]
    store.save(1, [10], configuration())
    assert len(store.list(1)) == 2
    assert store.rule(rule["id"])["starts_at"] == rule["starts_at"]
    assert store.list(2) == []
    assert store.toggle(2, [10], False) == 0
    assert store.toggle(1, [10], False) == 1
    assert not store.rule(rule["id"])["enabled"]
    changed = configuration()
    changed["archive"] = 60
    store.save_configuration(1, [10], changed, rule)
    with pytest.raises(ValueError, match="edited"):
        store.save_configuration(1, [10], configuration(), rule)
    assert not store.delete(2, rule["id"])
    assert store.delete(1, rule["id"])
    with pytest.raises(ValueError, match="removed"):
        store.save_configuration(1, [10], changed, rule)


def test_delivery_records_warnings_and_retired_ids(store):
    store.save(1, [10], configuration())
    rule = store.list(1)[0]
    source = NS(id=123, guild=NS(id=1), channel=NS(id=10))
    store.claim(source, rule)
    store.claim(source, rule)
    assert len(store.pending()) == 1
    store.welcome(123, "Original", 0xBDC3CA)
    assert store.welcome(123, "New", 0)["description"] == "Original"
    store.notice(source)
    store.notice(source)
    store.finish(123)
    assert store.pending() == []
    assert store.archive_rules(1, [123])[123]["archive"] == 1440
    store.toggle(1, [10], False)
    assert store.archive_rules(1, [123]) == {}
    store.delete(1, rule["id"])
    store.save(1, [10], configuration())
    assert store.list(1)[0]["id"] > rule["id"]


@pytest.mark.parametrize(
    "kind,mime,filename",
    [
        ("image", "image/png", "photo.png"),
        ("video", "video/mp4", "video.mp4"),
        ("gif", "image/gif", "picture.gif"),
    ],
)
def test_matching_attachment_types(kind, mime, filename):
    message = NS(
        content="",
        flags=NS(voice=False),
        embeds=[],
        attachments=[NS(content_type=mime, filename=filename)],
    )
    assert qualifies(message, configuration([kind]))
    assert not qualifies(message, configuration(["text"]))


def test_links_voice_enforcement_and_unicode_names():
    links = NS(
        content="https://tenor.com/view/example", flags=NS(voice=False), attachments=[], embeds=[]
    )
    assert qualifies(links, configuration(["links"]))
    assert not qualifies(links, dict(configuration(["links"]), require_attachment=True))
    voice = NS(content="", flags=NS(voice=True), attachments=[], embeds=[])
    assert qualifies(voice, configuration(["voice"]))
    with pytest.raises(ValueError, match="Pictures and Videos"):
        validate(dict(configuration(["gif"]), media_only=True))
    assert thread_name("Kylie 🧑‍💻") == "Kylie 🧑‍💻's thread"
    assert len(thread_name("🧑‍💻" * 100)) <= 100


@pytest.mark.asyncio
async def test_menu_budget_context_styling_and_ownership(store):
    user = NS(id=90, guild_permissions=NS(administrator=True, manage_guild=True))
    guild = NS(id=1)
    interaction = NS(
        guild=guild,
        guild_id=1,
        user=user,
        channel_id=10,
        channel=NS(),
        response=NS(send_message=AsyncMock()),
    )
    session = Session(NS(store=store), interaction)
    session.channels = [10]
    for page in (
        "main",
        "configure",
        "content",
        "uploads",
        "archive",
        "access",
        "welcome",
        "review",
        "rules",
        "all",
        "toggle",
        "delete",
    ):
        menu = Menu(session, page)
        assert menu.total_children_count <= 40
        assert menu.to_components()
        if page == "content":
            assert menu.children[0].children[0].content == private_heading("CONTENT & TRIGGERS")
        if page == "main":
            assert isinstance(menu.children[0].children[0], discord.ui.MediaGallery)
    menu = Menu(session)
    assert await menu.interaction_check(interaction)
    interaction.user = NS(id=91, guild_permissions=user.guild_permissions)
    assert not await menu.interaction_check(interaction)
    interaction.user = user
    session.generation += 1
    assert not await menu.interaction_check(interaction)


@pytest.mark.asyncio
async def test_only_new_posts_can_deliver_and_no_rule_means_no_actions(store):
    service = AutothreadService(NS(), store)
    service.deliver = AsyncMock()
    from unittest.mock import patch

    message = NS(
        id=100,
        guild=NS(id=1),
        channel=NS(id=10),
        type=discord.MessageType.default,
        webhook_id=None,
        author=NS(bot=False),
        flags=NS(voice=False),
        attachments=[],
        content="Text",
        created_at=datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC),
        embeds=[],
    )
    with patch("ky_bot.admin_functions.autothread.service.discord.TextChannel", NS):
        await service.on_message(message)
        service.deliver.assert_not_awaited()
        store.save(1, [10], configuration())
        await service.on_message(message)
        service.deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_only_tracks_bot_owned_threads_under_enabled_rules(store):
    store.save(1, [10], configuration())
    rule = store.list(1)[0]
    source = NS(id=123, guild=NS(id=1), channel=NS(id=10))
    store.claim(source, rule)
    store.finish(source.id)
    old = datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)

    async def history(**kwargs):
        yield NS(created_at=old)

    tracked = NS(
        id=123,
        parent_id=10,
        owner_id=900,
        archived=False,
        created_at=old,
        archive_timestamp=old,
        last_message_id=None,
        history=history,
        edit=AsyncMock(),
    )
    unrelated = NS(**vars(tracked))
    unrelated.id, unrelated.edit = 124, AsyncMock()
    guild = NS(id=1, active_threads=AsyncMock(return_value=[tracked, unrelated]))
    service = AutothreadService(NS(user=NS(id=900), guilds=[guild]), store)
    await service.archive_inactive()
    tracked.edit.assert_awaited_once()
    unrelated.edit.assert_not_awaited()
    assert tracked.edit.call_args.kwargs["archived"] is True
    store.toggle(1, [10], False)
    tracked.edit.reset_mock()
    service.archive_next_check = 0
    await service.archive_inactive()
    tracked.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_repeated_message_delivery_sends_one_welcome(store):
    from unittest.mock import patch

    store.save(1, [10], configuration())
    author = NS(id=90, bot=False, display_name="Kylie")

    async def history(**kwargs):
        for item in ():
            yield item

    thread = NS(
        owner_id=900,
        add_user=AsyncMock(),
        send=AsyncMock(),
        history=history,
    )
    guild = NS(id=1, get_thread=lambda mid: thread, get_member=lambda mid: author)
    permissions = NS(
        **{
            name: True
            for name in (
                "view_channel",
                "read_message_history",
                "create_public_threads",
                "send_messages_in_threads",
                "send_messages",
                "embed_links",
            )
        }
    )
    guild.me = NS(id=900)
    channel = NS(id=10, guild=guild, permissions_for=lambda member: permissions)
    message = NS(
        id=123,
        guild=guild,
        channel=channel,
        author=author,
        webhook_id=None,
        type=discord.MessageType.default,
        content="New post",
        attachments=[],
        flags=NS(voice=False),
        embeds=[],
        created_at=discord.utils.utcnow() + datetime.timedelta(seconds=1),
    )
    service = AutothreadService(NS(user=NS(id=900)), store)
    with (
        patch("ky_bot.admin_functions.autothread.service.discord.TextChannel", NS),
        patch("ky_bot.admin_functions.autothread.service.discord.Thread", NS),
    ):
        await service.on_message(message)
        await service.on_message(message)
    thread.send.assert_awaited_once()
    thread.add_user.assert_awaited_once()
    assert store.job(message.id)["done"] == 1
