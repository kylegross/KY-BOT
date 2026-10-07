"""Checklist parity, guild isolation and Components V2 attachment regressions."""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from PIL import Image

from ky_bot.checklists.artwork import board_artwork
from ky_bot.checklists.board import BoardView, page_entries
from ky_bot.checklists.categories import CategoryPicker, CategoryPrompt
from ky_bot.checklists.command import Checklists, LogChannelPicker
from ky_bot.checklists.manage import ItemControls, ManageCategories
from ky_bot.checklists.permissions import admin
from ky_bot.checklists.service import ChecklistService
from ky_bot.checklists.store import ChecklistStore
from ky_bot.checklists.task_actions import TaskActions


@pytest.fixture
def store(tmp_path):
    result = ChecklistStore(tmp_path / "checklists.sqlite3")
    yield result
    result.db.close()


def add_task(store, channel, source, category=None):
    store.add(channel, source, 90, f"Task {source}", [])
    task = next(t for t in store.category_tasks() if t["source"] == source)
    store.move_task(task["id"], category, task["revision"])
    return store.task(task["id"])


def test_persistence_isolation_and_completion_history(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    store = ChecklistStore(path)
    store.create(10, 1, 30)
    store.create(20, 2, 40)
    a = add_task(store, 10, 100)
    b = add_task(store, 20, 200)
    store.set_done(a["id"], 90, a["revision"], True)
    with pytest.raises(ValueError, match="changed"):
        store.edit_task(a["id"], a["revision"], title="stale edit")
    current = store.task(a["id"])
    store.delete_task(a["id"], 90, current["revision"])
    assert store.pending_logs()[0]["task"] == a["id"]
    assert store.tasks(10) == []
    assert store.tasks(20)[0]["id"] == b["id"]
    store.update_board(20, role_id=123, style="dark_silver")
    store.db.close()
    reopened = ChecklistStore(path)
    assert reopened.board(10)["log_channel"] == 30
    assert reopened.board(20)["log_channel"] == 40
    assert reopened.board(20)["role_id"] == 123
    assert reopened.board(20)["style"] == "dark_silver"
    assert len(reopened.pending_logs()) == 1
    reopened.db.close()


def test_order_and_priority_preserve_manual_order(store):
    store.create(10, 1, 0)
    category = store.add_category(10, "Events")
    a = add_task(store, 10, 100, category)
    b = add_task(store, 10, 200, category)
    store.edit_task(b["id"], b["revision"], priority="high")
    assert [t["id"] for t in store.tasks(10)] == [a["id"], b["id"]]
    b = store.task(b["id"])
    store.reorder(10, "task", b["id"], "priority", b["revision"])
    assert [t["id"] for t in store.tasks(10)] == [b["id"], a["id"]]


def test_authorized_role_is_per_board():
    member = SimpleNamespace(
        bot=False,
        roles=[SimpleNamespace(id=99)],
        guild_permissions=SimpleNamespace(administrator=False, manage_guild=False),
    )
    assert admin(member, {"role_id": 99})
    assert not admin(member, {"role_id": 88})
    assert not admin(member)
    member.bot = True
    assert not admin(member, {"role_id": 99})


@pytest.mark.asyncio
async def test_inline_board_budget_counts_and_persistence(store):
    store.create(10, 1, 0)
    for i in range(8):
        cat = store.add_category(10, f"Category {i}")
        add_task(store, 10, 100 + i, cat)
    service = SimpleNamespace(store=store)
    board = store.board(10)
    view = BoardView(service, board)
    assert view.total_children_count <= 40
    assert view.is_persistent()
    items = list(view.walk_children())
    labels = [i.content for i in items if isinstance(i, discord.ui.TextDisplay)]
    assert sum("*1 open task*" in text for text in labels) == 4
    task_buttons = [
        i for i in items if isinstance(i, discord.ui.Button) and ":task:" in (i.custom_id or "")
    ]
    assert len(task_buttons) == 4
    assert all(isinstance(b.parent, discord.ui.Section) for b in task_buttons)
    _, page, pages = page_entries(board, store.tasks(10), store.categories(10))
    assert (page, pages) == (0, 2)
    assert all(
        not item.description
        for item in items
        if isinstance(item, discord.ui.MediaGallery)
        for item in item.items
    )


@pytest.mark.asyncio
async def test_refresh_reuploads_every_referenced_image(store, monkeypatch):
    store.create(10, 1, 0)
    store.update_board(10, message=700)
    add_task(store, 10, 100)
    channel = SimpleNamespace(guild=SimpleNamespace(id=1), get_partial_message=lambda _: message)
    message = SimpleNamespace(edit=AsyncMock())
    service = ChecklistService(SimpleNamespace(), store)
    service.channel = AsyncMock(return_value=channel)
    service.priority_dots.for_guild = AsyncMock(
        return_value={"high": "🔴", "medium": "🟡", "low": "🟢"}
    )
    monkeypatch.setattr(
        "ky_bot.checklists.artwork.board_artwork",
        lambda *args: {
            "checklist_header.png": b"header",
            "checklist_footer.png": b"footer",
            "category_none.png": b"category",
        },
    )
    await service.render_board(store.board(10))
    kwargs = message.edit.call_args.kwargs
    assert kwargs["content"] is None and kwargs["embeds"] == []
    assert {file.filename for file in kwargs["attachments"]} == {
        "checklist_header.png",
        "checklist_footer.png",
        "category_none.png",
    }
    assert store.board(10)["dirty"] == 0


@pytest.mark.asyncio
async def test_private_task_buttons_inline(store):
    store.create(10, 1, 0)
    task = add_task(store, 10, 100)
    view = TaskActions(SimpleNamespace(store=store), task)
    buttons = [i for i in view.walk_children() if isinstance(i, discord.ui.Button)]
    assert len(buttons) == 6
    assert all(isinstance(button.parent, discord.ui.Section) for button in buttons)


def test_artwork_uses_finish_and_only_current_page(store):
    from ky_bot.design.typography import TypographyError, font_file

    try:
        font_file("display")
    except TypographyError:
        pytest.skip("Licensed VONCA fonts are not distributed with the test suite.")
    store.create(10, 1, 0)
    for i in range(12):
        store.add_category(10, f"Category {i}")
    for style in ("gold", "dark_silver", "silver_neon"):
        store.update_board(10, style=style)
        board = store.board(10)
        artwork = board_artwork(board, store.categories(10), [])
        assert len(artwork) == 6
        assert Image.open(io.BytesIO(artwork["checklist_header.png"])).size == (1600, 280)
        assert Image.open(io.BytesIO(artwork["checklist_footer.png"])).size == (1600, 150)
        assert all(Image.open(io.BytesIO(data)).mode == "RGBA" for data in artwork.values())


def setup_interaction():
    return SimpleNamespace(
        guild_id=1,
        channel_id=10,
        channel=SimpleNamespace(id=10),
        user=SimpleNamespace(id=90, guild_permissions=SimpleNamespace(manage_guild=True)),
        response=SimpleNamespace(defer=AsyncMock(), send_message=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
        edit_original_response=AsyncMock(),
        message=SimpleNamespace(edit=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_yes_waits_for_channel_selection_and_no_skips_picker():
    log = SimpleNamespace(id=30, guild=SimpleNamespace(id=1))
    cog = SimpleNamespace(
        service=SimpleNamespace(check_permissions=Mock(), channel=AsyncMock(return_value=log)),
        apply_setup=AsyncMock(return_value="Created with log channel"),
    )
    interaction = setup_interaction()
    await Checklists.checklist.callback(cog, interaction, checklist_log="yes", title="Tasks")
    cog.apply_setup.assert_not_awaited()
    menu = interaction.followup.send.call_args.kwargs["view"]
    assert isinstance(menu, LogChannelPicker)
    assert await menu.interaction_check(interaction)
    picker = menu.children[0]
    picker._values = [log]
    await picker.callback(interaction)
    args = cog.apply_setup.call_args.args
    assert args[:2] == (10, 1)
    assert args[2] == {"name": "Tasks", "log_channel": 30}
    assert menu.completed
    assert not await menu.interaction_check(interaction)
    cog.apply_setup.reset_mock()
    no = setup_interaction()
    await Checklists.checklist.callback(cog, no, checklist_log="no")
    assert cog.apply_setup.call_args.args[2]["log_channel"] == 0
    assert "view" not in no.followup.send.call_args.kwargs


@pytest.mark.asyncio
async def test_picker_rejects_same_channel_and_other_users():
    log = SimpleNamespace(id=10, guild=SimpleNamespace(id=1))
    cog = SimpleNamespace(
        service=SimpleNamespace(check_permissions=Mock(), channel=AsyncMock(return_value=log)),
        apply_setup=AsyncMock(),
    )
    menu = LogChannelPicker(cog, 90, 10, 1, {}, [])
    interaction = setup_interaction()
    interaction.user.id = 91
    assert not await menu.interaction_check(interaction)
    interaction.user.id = 90
    menu.children[0]._values = [log]
    await menu.children[0].callback(interaction)
    cog.apply_setup.assert_not_awaited()
    assert "separate" in interaction.edit_original_response.call_args.kwargs["content"]


@pytest.mark.asyncio
async def test_no_log_events_are_never_forwarded_later(store):
    store.create(10, 1, 0)
    task = add_task(store, 10, 100)
    store.set_done(task["id"], 90, task["revision"], True)
    assert store.pending_logs()[0]["log_channel"] == 0
    store.update_board(10, log_channel=30, dirty=0)
    store.cleaned(task["id"])
    service = ChecklistService(SimpleNamespace(), store)
    service.sync_task_threads = AsyncMock()
    service.category_prompts = AsyncMock()
    service.channel = AsyncMock()
    await service.tick()
    service.channel.assert_not_awaited()
    assert store.pending_logs() == []
    assert store.db.execute("SELECT COUNT(*) FROM logs").fetchone()[0] == 1


@pytest.mark.asyncio
async def test_source_is_retained_if_text_changed_after_capture(store):
    store.create(10, 1, 0)
    task = add_task(store, 10, 100)
    store.update_board(10, dirty=0, message=77)
    source = SimpleNamespace(
        author=SimpleNamespace(id=90, bot=False),
        content="Changed after capture",
        attachments=[],
        delete=AsyncMock(),
    )
    channel = SimpleNamespace(fetch_message=AsyncMock(return_value=source))
    service = ChecklistService(SimpleNamespace(), store)
    service.channel = AsyncMock(return_value=channel)
    with pytest.raises(ValueError, match="changed"):
        await service.cleanup_source(task)
    source.delete.assert_not_awaited()
    assert len(store.pending_cleanup()) == 1


@pytest.mark.asyncio
async def test_private_forms_use_inline_buttons(store):
    store.create(10, 1, 0)
    store.add_category(10, "Events")
    task = add_task(store, 10, 100)
    service = SimpleNamespace(store=store)
    for view in (
        CategoryPicker(service, task),
        CategoryPrompt(service, task),
        ItemControls(service, task),
        ManageCategories(service, 10),
    ):
        assert isinstance(view, discord.ui.LayoutView)
        assert view.total_children_count <= 40
        buttons = [item for item in view.walk_children() if isinstance(item, discord.ui.Button)]
        assert buttons and all(isinstance(b.parent, discord.ui.Section) for b in buttons)
        assert view.to_components()


@pytest.mark.asyncio
async def test_completion_logs_route_to_each_boards_channel(store):
    for channel, guild, log in ((10, 1, 30), (20, 2, 40)):
        store.create(channel, guild, log)
        task = add_task(store, channel, channel + 100)
        store.set_done(task["id"], 90, task["revision"], True)
        store.cleaned(task["id"])
        store.update_board(channel, dirty=0)
    targets = {
        30: SimpleNamespace(guild=SimpleNamespace(id=1), send=AsyncMock()),
        40: SimpleNamespace(guild=SimpleNamespace(id=2), send=AsyncMock()),
    }
    service = ChecklistService(SimpleNamespace(), store)
    service.sync_task_threads = AsyncMock()
    service.category_prompts = AsyncMock()
    service.channel = AsyncMock(side_effect=lambda cid: targets[cid])
    await service.tick()
    targets[30].send.assert_awaited_once()
    targets[40].send.assert_awaited_once()
    assert store.pending_logs() == []


def test_slash_logging_choice_is_yes_or_no_and_required():
    command = Checklists.create.get_command("checklist")
    parameter = next(p for p in command.parameters if p.name == "checklist_log")
    assert parameter.required
    assert [(choice.name, choice.value) for choice in parameter.choices] == [
        ("Yes", "yes"),
        ("No", "no"),
    ]
