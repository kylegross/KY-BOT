import datetime
import os
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from ky_bot.admin_functions.autoreact.command import CATALOG, Menu, Session
from ky_bot.admin_functions.autoreact.service import AutoReactService, matches, parse_emojis
from ky_bot.admin_functions.autoreact.store import Store, defaults


@pytest.fixture(params=["sqlite", "postgres"])
def store(request, tmp_path):
    if request.param == "postgres":
        if not os.getenv("TEST_DATABASE_URL"):
            pytest.skip("Dedicated PostgreSQL test database not configured.")
        from ky_bot.admin_functions.autoreact.postgres import PostgresStore

        result = PostgresStore(os.environ["TEST_DATABASE_URL"])
        result.db.connection.execute("TRUNCATE autoreact_rules, autoreact_jobs")
    else:
        result = Store(tmp_path / "react.sqlite3")
    yield result
    result.db.close()


def settings():
    return dict(defaults(), emojis=["💛", "✨"])


def test_rules_isolated_and_stale_mutations_atomic(store):
    assert store.list(1) == []
    store.save(1, [10, 20], settings(), {10: None, 20: None})
    assert store.list(2) == []
    old = store.get(1, 10)
    store.action(1, {10: old["revision"]}, "off")
    with pytest.raises(ValueError):
        store.save(1, [20, 10], settings(), {20: store.get(1, 20)["revision"], 10: old["revision"]})
    assert store.get(1, 20)["settings"] == settings()
    current = store.get(1, 10)
    store.save(1, [10], dict(settings(), emojis=["👍"]), {10: current["revision"]})
    assert not store.get(1, 10)["enabled"]
    assert store.get(1, 10)["starts"] == old["starts"]
    with pytest.raises(ValueError):
        store.action(2, {10: current["revision"]}, "remove")
    store.action(1, {10: store.get(1, 10)["revision"]}, "remove")
    assert store.get(1, 10) is None


def test_unicode_and_custom_emoji_validation():
    guild = NS(emojis=[], get_emoji=lambda _: None)
    assert parse_emojis("💛 💛 👩🏽‍💻 🇺🇸", guild) == ["💛", "👩🏽‍💻", "🇺🇸"]
    with pytest.raises(ValueError):
        parse_emojis("hello", guild)
    with pytest.raises(ValueError):
        parse_emojis("<:unknown:123>", guild)
    assert len(CATALOG) == 3944


def message():
    channel = Mock(spec=discord.TextChannel)
    channel.id, channel.name = 10, "tasks"
    channel.guild = NS(id=1, me=NS())
    channel.permissions_for.return_value = NS(
        view_channel=True, read_message_history=True, add_reactions=True
    )
    msg = NS(
        id=100,
        guild=channel.guild,
        channel=channel,
        webhook_id=None,
        author=NS(bot=False),
        type=discord.MessageType.default,
        content="hello",
        attachments=[],
        embeds=[],
        flags=NS(voice=False),
        created_at=datetime.datetime.now(datetime.UTC),
        add_reaction=AsyncMock(),
    )
    channel.fetch_message = AsyncMock(return_value=msg)
    return msg


async def test_order_checkpoint_duplicate_enqueue_and_rule_removal(store):
    store.save(1, [10], settings(), {10: None})
    msg = message()
    client = NS(get_channel=lambda _: msg.channel)
    service = AutoReactService(client, store)
    await service.on_message(msg)
    await service.on_message(msg)
    assert len(store.jobs()) == 1
    job = store.jobs()[0]
    await service.deliver(job)
    assert [c.args[0] for c in msg.add_reaction.await_args_list] == ["💛", "✨"]
    assert not store.jobs()
    msg.id = 101
    await service.on_message(msg)
    job = store.jobs()[0]
    store.action(1, {10: store.get(1, 10)["revision"]}, "remove")
    await service.deliver(job)
    assert msg.add_reaction.await_count == 2


async def test_retry_resumes_at_checkpoint(store):
    store.save(1, [10], settings(), {10: None})
    msg = message()
    msg.add_reaction.side_effect = [None, OSError("temporary connection failure")]
    service = AutoReactService(NS(get_channel=lambda _: msg.channel), store)
    await service.on_message(msg)
    await service.deliver(store.jobs()[0])
    store.update_job(msg.id, retry=0)
    job = store.jobs()[0]
    assert job["position"] == 1
    assert job["attempts"] == 1
    msg.add_reaction.reset_mock(side_effect=True)
    await service.deliver(job)
    msg.add_reaction.assert_awaited_once_with("✨")
    assert not store.jobs()


def test_postgres_adapter_uses_isolated_tables_and_upsert():
    from ky_bot.admin_functions.autoreact.postgres import Connection

    driver = Mock()
    connection = Connection(driver)
    connection.execute("INSERT OR REPLACE INTO rules VALUES (?,?,?,?,?,?,?)", tuple(range(7)))
    statement, parameters = driver.execute.call_args.args
    assert "INSERT INTO autoreact_rules" in statement
    assert "ON CONFLICT(guild,channel) DO UPDATE" in statement
    assert parameters == tuple(range(7))
    connection.execute("INSERT OR IGNORE INTO jobs(message) VALUES (?)", (10,))
    assert "autoreact_jobs" in driver.execute.call_args.args[0]
    assert "ON CONFLICT DO NOTHING" in driver.execute.call_args.args[0]


def test_filters_distinguish_webhooks_and_bots():
    msg = message()
    assert matches(msg, settings())
    msg.author.bot = True
    assert not matches(msg, settings())
    msg.webhook_id = 42
    assert matches(msg, dict(settings(), webhooks=True))
    assert not matches(msg, dict(settings(), bots=True))


async def test_all_menu_pages_serialize_and_order(store):
    guild = NS(id=1, emojis=[], get_channel=lambda _: NS(), get_member=lambda _: NS())
    s = Session(NS(store=store), NS(guild=guild, user=NS(id=7), channel=None))
    s.channels, s.draft = [10], settings()
    for cid in range(10, 16):
        store.save(1, [cid], settings(), {cid: None})
    for page in (
        "main",
        "configure",
        "channels",
        "emojis",
        "browse",
        "server",
        "content",
        "filters",
        "review",
        "rules",
        "all",
        "toggle",
        "remove",
    ):
        s.page = 0
        view = Menu(s, page)
        assert view.total_children_count <= 40
        assert view.to_components()
    titles = [c.content for c in Menu(s).walk_children() if isinstance(c, discord.ui.TextDisplay)]
    ordered = ["SAVED RULES", "SERVER RULES", "REMOVE RULE", "TURN ON / OFF"]
    indexes = [next(n for n, t in enumerate(titles) if f"**{label}**" in t) for label in ordered]
    assert indexes == sorted(indexes)
