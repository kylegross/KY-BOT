import time
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import discord
import pytest

from ky_bot.checklists.service import ChecklistService
from ky_bot.checklists.store import ChecklistStore
from ky_bot.checklists.subtasks import DiscussionPanel, validate


@pytest.fixture
def store(tmp_path):
    store = ChecklistStore(tmp_path / "checklist.sqlite3")
    store.create(10, 1, 20)
    store.add(10, 100, 90, "Parent", [])
    task = store.category_tasks()[0]
    store.move_task(task["id"], None, task["revision"])
    yield store
    store.db.close()


def parent(store):
    return store.tasks(10)[0]


def test_parent_completion_requires_remaining_subtasks(store):
    task = parent(store)
    store.add_subtask(task["id"], "First", 90)
    store.add_subtask(task["id"], "Second", 90)
    first, second = store.subtasks(task["id"])
    with pytest.raises(ValueError, match="unfinished"):
        store.set_done(task["id"], 90, task["revision"], True)
    assert store.pending_logs() == []
    store.change_subtask(first["id"], first["revision"], done=True)
    with pytest.raises(ValueError, match="unfinished"):
        store.set_done(task["id"], 90, task["revision"], True)
    store.change_subtask(second["id"], second["revision"], delete=True)
    assert store.pending_logs() == []  # Sub-task actions do not emit completion/server events.
    store.set_done(task["id"], 90, task["revision"], True)
    assert len(store.pending_logs()) == 1
    with pytest.raises(ValueError, match="Reopen"):
        store.add_subtask(task["id"], "More", 90)
    with pytest.raises(ValueError, match="Reopen"):
        store.change_subtask(first["id"], 1, done=False)
    store.set_done(task["id"], 90, store.task(task["id"])["revision"], False)
    store.change_subtask(first["id"], 1, done=False)
    with pytest.raises(ValueError, match="unfinished"):
        store.set_done(task["id"], 90, store.task(task["id"])["revision"], True)


def test_deleted_all_subtasks_allows_completion(store):
    task = parent(store)
    store.add_subtask(task["id"], "Delete me", 90)
    item = store.subtasks(task["id"])[0]
    store.change_subtask(item["id"], item["revision"], delete=True)
    store.set_done(task["id"], 90, task["revision"], True)
    assert store.task(task["id"])["done"] == 1


def test_subtask_edit_order_staleness_and_persistence(store):
    task = parent(store)
    for title in ("One", "Two", "Three"):
        store.add_subtask(task["id"], title, 90)
    first = store.subtasks(task["id"])[0]
    store.change_subtask(first["id"], first["revision"], move="down")
    assert [s["title"] for s in store.subtasks(task["id"])] == ["Two", "One", "Three"]
    with pytest.raises(ValueError, match="changed"):
        store.change_subtask(first["id"], first["revision"], title="Stale")
    with pytest.raises(ValueError, match="Up or Down"):
        store.change_subtask(first["id"], store.subtask(first["id"])["revision"], move="invalid")
    current = store.subtask(first["id"])
    store.change_subtask(first["id"], current["revision"], title="Changed")
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    reopened = ChecklistStore(path)
    try:
        assert reopened.subtask(first["id"])["title"] == "Changed"
    finally:
        reopened.db.close()


@pytest.mark.asyncio
async def test_thread_panel_persists_and_has_green_inline_add(store):
    task = parent(store)
    store.thread(task["id"], 30, seeded=True)
    for i in range(10):
        store.add_subtask(task["id"], f"Sub-task {i}", 90)
    view = DiscussionPanel(NS(store=store), store.task(task["id"]))
    assert view.is_persistent()
    assert len(list(view.walk_children())) <= 40
    buttons = [i for i in view.walk_children() if isinstance(i, discord.ui.Button)]
    add = next(b for b in buttons if b.label == "✚ Add sub-task")
    assert add.style == discord.ButtonStyle.success
    assert isinstance(add.parent, discord.ui.Section)
    assert sum(b.label == "Manage" for b in buttons) == 4
    member = NS(bot=False, guild_permissions=NS(manage_guild=True, administrator=False))
    service = NS(store=store)
    assert validate(service, NS(guild=NS(id=1), channel_id=30, user=member), task["id"])
    for guild, channel in ((2, 30), (1, 31)):
        with pytest.raises(ValueError, match="administrators"):
            validate(service, NS(guild=NS(id=guild), channel_id=channel, user=member), task["id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("age,archived", [(86300, False), (86500, True)])
async def test_archive_after_24_hours_without_destroying_task(store, age, archived):
    task = parent(store)
    store.thread(task["id"], 30, seeded=True)
    store.discussion_rendered(task["id"], 99, task["discussion_revision"])
    store.touch_thread(task["id"], time.time() - age)
    thread = NS(archived=False, last_message_id=None, edit=AsyncMock())
    service = ChecklistService(NS(), store)
    service.channel = AsyncMock(return_value=thread)
    await service.sync_discussions()
    assert thread.edit.await_count == int(archived)
    if archived:
        assert thread.edit.call_args.kwargs["archived"] is True
        assert thread.edit.call_args.kwargs["locked"] is False
    assert store.task(task["id"])["done"] == 0


@pytest.mark.asyncio
async def test_new_message_resets_inactivity(store):
    task = parent(store)
    store.thread(task["id"], 30, seeded=True)
    store.discussion_rendered(task["id"], 99, task["discussion_revision"])
    store.touch_thread(task["id"], time.time() - 90000)
    thread = NS(archived=False, last_message_id=None, edit=AsyncMock())
    service = ChecklistService(NS(), store)
    message = NS(guild=NS(id=1), channel=NS(id=30), author=NS(bot=False))
    # Exercise the thread branch without constructing a Discord client/channel.
    from unittest.mock import patch

    with patch("ky_bot.checklists.service.discord.Thread", NS):
        await service.on_message(message)
    service.channel = AsyncMock(return_value=thread)
    await service.sync_discussions()
    thread.edit.assert_not_awaited()


@pytest.mark.asyncio
async def test_opening_archived_discussion_reuses_thread_and_resets_timer(store):
    task = parent(store)
    store.thread(task["id"], 30, seeded=True)
    store.touch_thread(task["id"], time.time() - 90000)
    thread = NS(id=30, archived=True, edit=AsyncMock(), add_user=AsyncMock())
    service = ChecklistService(NS(), store)
    service.channel = AsyncMock(return_value=thread)
    service.render_discussion = AsyncMock()
    channel = NS(
        permissions_for=lambda member: NS(view_channel=True),
        create_thread=AsyncMock(),
    )
    opened = await service.ensure_thread(store.task(task["id"]), channel, NS(id=90))
    assert opened.id == 30
    channel.create_thread.assert_not_awaited()
    assert thread.edit.call_args.kwargs["archived"] is False
    assert thread.edit.call_args.kwargs["auto_archive_duration"] == 1440
    assert store.task(task["id"])["thread_activity"] > time.time() - 5
