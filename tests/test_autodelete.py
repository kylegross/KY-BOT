import datetime
import io
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord
import pytest
from PIL import Image

from ky_bot.admin_functions.autodelete.autodelete_logging import ActivityLogs, snapshot
from ky_bot.admin_functions.autodelete.autodelete_service import delete_matching, matches
from ky_bot.admin_functions.autodelete.autodelete_store import RuleStore
from ky_bot.admin_functions.autodelete.command import Menu, Session
from ky_bot.admin_functions.autoreact.command import artwork as reaction_artwork
from ky_bot.admin_functions.autothread.command import artwork as thread_artwork


@pytest.fixture
def store(tmp_path):
    result = RuleStore(tmp_path / "deletion.db")
    yield result
    result.db.close()


def test_rules_paused_edit_isolation_and_start_boundary(store):
    store.save(1, [10], ["messages_all"], 300, "5 minutes", 90)
    rule = store.list(1)[0]
    assert rule["enabled"] and rule["starts_at"] > 0
    assert store.list(2) == []
    store.toggle(1, [10], False)
    rule = store.list(1)[0]
    store.save(1, [10], ["messages_all"], 300, "5 minutes", 90)
    assert not store.list(1)[0]["enabled"]
    store.update_rule(1, rule, ["messages_user"], 600, "10 minutes", 90)
    current = store.list(1)[0]
    assert current["starts_at"] == rule["starts_at"] and not current["enabled"]
    with pytest.raises(ValueError):
        store.update_rule(1, rule, ["messages_all"], 300, "5 minutes", 90)
    assert not store.delete(2, current["id"])
    assert store.delete(1, current["id"], current)


async def test_deletion_age_boundary_and_pause(store):
    store.save(1, [10], ["messages_all"], 60, "1 minute", 90)
    rule = store.list(1)[0]
    now = discord.utils.utcnow()
    message = NS(
        created_at=now - datetime.timedelta(seconds=61),
        attachments=[],
        embeds=[],
        author=NS(bot=False),
        delete=AsyncMock(),
    )
    # The rule must never backfill messages posted before it was enabled.
    assert not await delete_matching(message, [rule], store=store)
    message.created_at = now - datetime.timedelta(seconds=2)
    assert not await delete_matching(message, [rule], store=store)
    with store.db:
        store.db.execute(
            "UPDATE rules SET starts_at=? WHERE id=?",
            ((now - datetime.timedelta(seconds=120)).timestamp(), rule["id"]),
        )
    eligible = store.list(1)[0]
    message.created_at = now - datetime.timedelta(seconds=61)
    assert await delete_matching(message, [eligible], store=store) == 1
    store.toggle(1, [10], False)
    message.delete.reset_mock()
    assert not await delete_matching(message, [eligible], store=store)
    message.delete.assert_not_awaited()


def test_deleted_rule_ids_and_old_criteria_cannot_resume(store):
    store.save(1, [10], ["messages_all"], 60, "1 minute", 90)
    old = store.list(1)[0]
    store.update_rule(1, old, ["messages_user"], 60, "1 minute", 90)
    assert not store.enabled(old["id"], old)
    current = store.list(1)[0]
    store.delete(1, current["id"], current)
    store.save(1, [10], ["messages_all"], 60, "1 minute", 90)
    assert store.list(1)[0]["id"] > old["id"]
    assert not store.enabled(old["id"], old)


def test_bot_media_keep_overrides_broad_message_filters():
    message = NS(author=NS(bot=True), attachments=[], embeds=[])
    assert not matches(message, ["messages_all", "botmedia_keep_image"], kinds={"image", "video"})
    assert matches(message, ["messages_user", "botmedia_delete_image"], kinds={"image"})


def test_logs_do_not_auto_enable_or_retain_private_media(store):
    client = NS()
    logs = ActivityLogs(client, store)
    assert logs.config(1, 10) is None
    store.save(1, [10], ["messages_all"], 60, "1 minute", 90)
    logs.save(1, 10, 20, ["messages", "deletes"])
    assert logs.config(2, 10) is None
    message = NS(
        attachments=[
            NS(
                content_type="image/png",
                filename="private.png",
                url="https://example.com/private.png",
                proxy_url=None,
            )
        ],
        embeds=[],
        content="https://example.com/private.png",
        mentions=[],
        role_mentions=[],
        guild=NS(get_member=lambda _: None),
        author=NS(id=1, display_name="Example"),
        created_at=discord.utils.utcnow(),
    )
    result = snapshot(message)
    assert "private.png" not in str(result)
    assert result["gifs"] == []


async def test_all_menu_component_budgets_and_approved_order(store):
    user = NS(id=90, guild_permissions=NS(manage_guild=True, administrator=True))
    guild = NS(id=1, get_channel=lambda _: None, get_member=lambda _: user)
    cog = NS(store=store, service=NS())
    s = Session(cog, NS(guild=guild, user=user, channel=None, channel_id=10))
    s.channels, s.content = [10], ["messages_all"]
    for n in range(7):
        store.save(1, [10], ["messages_all"], 60 + n, f"{n} minutes", 90)
    s.original = store.list(1)[0]
    for page in (
        "main",
        "configure",
        "clean",
        "channels",
        "content",
        "time",
        "review",
        "confirm_clean",
        "rules",
        "all",
        "remove",
        "toggle",
        "logs",
        "log_review",
    ):
        menu = Menu(s, page)
        assert menu.total_children_count <= 40
        assert menu.to_components()
    headings = [c.content for c in Menu(s).walk_children() if isinstance(c, discord.ui.TextDisplay)]
    titles = [
        "DESTINATION",
        "CONFIGURE RULES",
        "SAVED RULES",
        "SERVER RULES",
        "TURN ON / OFF",
        "CLEAN UP NOW",
        "ACTIVITY LOGS",
    ]
    positions = [next(n for n, t in enumerate(headings) if f"**{label}**" in t) for label in titles]
    assert positions == sorted(positions)


@pytest.mark.parametrize("factory", [thread_artwork, reaction_artwork])
def test_default_automation_footer_is_a_banner_not_icon(factory):
    uploads = factory()
    try:
        footer = next(f for f in uploads if f.filename == "dark_silver_icon.png")
        data = footer.fp.read()
        assert Image.open(io.BytesIO(data)).size == (1600, 150)
    finally:
        for upload in uploads:
            upload.close()
