"""Integration tests opt in only with a dedicated disposable TEST_DATABASE_URL."""

import asyncio
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from ky_bot.admin_functions.settings_repository import open_settings
from ky_bot.checklists.board import category_heading
from ky_bot.checklists.store import ChecklistStore
from ky_bot.config import ConfigurationError, Settings
from ky_bot.storage.migrate import (
    CHECKLIST_TABLES,
    SETTINGS_TABLES,
    import_snapshot,
    snapshot,
)
from ky_bot.storage.postgres import PostgresChecklistStore, Record, postgres_sql
from ky_bot.storage.server_data import ServerChecklistData


@pytest.fixture
def event_loop_policy():
    if sys.platform == "win32":
        return asyncio.WindowsSelectorEventLoopPolicy()
    return asyncio.DefaultEventLoopPolicy()


def test_adapter_preserves_named_and_positional_records():
    row = Record(id=1550000000000000000, name="Example")
    assert row[0] == row["id"]
    assert dict(row)["name"] == row[1]
    assert postgres_sql("INSERT OR IGNORE INTO tasks(source) VALUES (?)") == (
        "INSERT INTO tasks(source) VALUES (%s) ON CONFLICT DO NOTHING"
    )


def test_database_url_is_validated_and_hidden(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "test-value")
    monkeypatch.setenv("COMMAND_SYNC", "none")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@localhost/test")
    settings = Settings.from_env(Path("nonexistent-env"))
    assert settings.database_url
    assert "secret" not in repr(settings)
    monkeypatch.setenv("DATABASE_URL", "https://example.com")
    with pytest.raises(ConfigurationError, match="PostgreSQL"):
        Settings.from_env(Path("nonexistent-env"))


def test_panel_acknowledgement_retains_newer_changes(tmp_path):
    store = ChecklistStore(tmp_path / "tasks.sqlite3")
    try:
        store.create(10, 1, 20)
        rendered = store.board(10)
        category = store.add_category(10, "Administration")
        store.add(10, 100, 90, "Original", [])
        task = store.category_tasks()[0]
        store.move_task(task["id"], category, task["revision"])
        task = store.task(task["id"])
        assert "Administration" in category_heading(store, task)
        store.rendered(10, 999, rendered["revision"])
        assert store.board(10)["dirty"] == 1
        store.rendered(10, 999, store.board(10)["revision"])
        assert store.board(10)["dirty"] == 0
    finally:
        store.db.close()


@pytest.fixture
def pg_store():
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set TEST_DATABASE_URL to a disposable PostgreSQL database.")
    store = PostgresChecklistStore(url)
    # Integration tests are destructive only inside this explicitly designated test database.
    store.db.connection.execute(
        "TRUNCATE boards,tasks,categories,logs,subtasks,guild_settings,"
        "welcome_settings,welcome_artwork "
        "RESTART IDENTITY"
    )
    yield store
    store.db.close()


def test_two_process_repositories_share_data_and_reject_stale_changes(pg_store):
    second = PostgresChecklistStore(os.environ["TEST_DATABASE_URL"])
    try:
        pg_store.create(10, 1, 20)
        pg_store.create(30, 2, 40)
        cid = pg_store.add_category(10, "Operations")
        assert pg_store.add(10, 1550000000000000000, 90, "Task", [])
        assert not second.add(10, 1550000000000000000, 90, "Duplicate", [])
        task = pg_store.category_tasks()[0]
        pg_store.move_task(task["id"], cid, task["revision"])
        task = pg_store.task(task["id"])
        assert second.tasks(30) == []
        second.edit_task(task["id"], task["revision"], title="Changed from web")
        web = ServerChecklistData(second, 1)
        assert web.tasks(10)[0]["title"] == "Changed from web"
        with pytest.raises(ValueError, match="authorized server"):
            web.tasks(30)
        assert pg_store.task(task["id"])["title"] == "Changed from web"
        with pytest.raises(ValueError, match="changed"):
            pg_store.set_done(task["id"], 90, task["revision"], True)
        current = pg_store.task(task["id"])

        def complete(store):
            try:
                store.set_done(current["id"], 90, current["revision"], True)
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(complete, [pg_store, second]))
        assert sorted(results) == [False, True]
        assert len(pg_store.pending_logs()) == 1
        assert pg_store.pending_logs()[0]["log_channel"] == 20
    finally:
        second.db.close()


async def test_postgres_settings_are_shared_and_keep_artwork(pg_store):
    url = os.environ["TEST_DATABASE_URL"]
    async with open_settings(Path(":memory:"), database_url=url) as first:
        await first.set_welcome_message(1, "Hello {member}")
        await first.set_welcome_background(1, b"example-image")
        await first.set_log_channel(1, 20)
        async with open_settings(Path(":memory:"), database_url=url) as second:
            assert (await second.get(1)).welcome_message == "Hello {member}"
            assert (await second.get_welcome_artwork(1)).background == b"example-image"
            assert (await second.get(2)).log_channel_id is None


def test_postgres_subtasks_block_parent_and_survive_other_connections(pg_store):
    second = PostgresChecklistStore(os.environ["TEST_DATABASE_URL"])
    try:
        pg_store.create(10, 1, 20)
        pg_store.add(10, 100, 90, "Parent", [])
        task = pg_store.category_tasks()[0]
        pg_store.move_task(task["id"], None, task["revision"])
        task = pg_store.task(task["id"])
        pg_store.add_subtask(task["id"], "Remaining", 90)
        child = second.subtasks(task["id"])[0]
        with pytest.raises(ValueError, match="unfinished"):
            second.set_done(task["id"], 90, task["revision"], True)
        second.change_subtask(child["id"], child["revision"], delete=True)
        pg_store.set_done(task["id"], 90, task["revision"], True)
        assert second.task(task["id"])["done"] == 1
        assert len(second.pending_logs()) == 1
        assert second.subtasks(task["id"]) == []
    finally:
        second.db.close()


async def test_migration_preserves_ids_history_and_source_files(pg_store, tmp_path):
    settings_file = tmp_path / "settings.sqlite3"
    checklist_file = tmp_path / "checklist.sqlite3"
    async with open_settings(settings_file) as settings:
        await settings.set_welcome_background(1, b"image-bytes")
        await settings.set_welcome_message(1, "Welcome!")
    source = ChecklistStore(checklist_file)
    source.create(10, 1, 20)
    source.add(10, 1550000000000000000, 90, "Task", [])
    task = source.category_tasks()[0]
    source.move_task(task["id"], None, task["revision"])
    source.set_done(task["id"], 90, source.task(task["id"])["revision"], True)
    source.db.close()
    original = (settings_file.read_bytes(), checklist_file.read_bytes())
    data = snapshot(settings_file, SETTINGS_TABLES) | snapshot(checklist_file, CHECKLIST_TABLES)
    import_snapshot(pg_store.db.connection, data)
    assert pg_store.task(task["id"])["source"] == 1550000000000000000
    assert pg_store.task(task["id"])["done"] == 1
    assert len(pg_store.pending_logs()) == 1
    assert (settings_file.read_bytes(), checklist_file.read_bytes()) == original
    with pytest.raises(ValueError, match="not empty"):
        import_snapshot(pg_store.db.connection, data)
    pg_store.add(10, 200, 90, "New task", [])
    assert max(t["id"] for t in pg_store.category_tasks()) > task["id"]


def test_failed_import_rolls_back_all_records(pg_store):
    data = {
        "boards": (["channel", "guild", "log_channel"], [(10, 1, 20)]),
        "tasks": (
            ["channel", "source", "author", "title", "attachments"],
            [
                (10, 100, 90, "One", "[]"),
                (10, 100, 90, "Duplicate source", "[]"),
            ],
        ),
    }
    import psycopg

    with pytest.raises(psycopg.errors.UniqueViolation):
        import_snapshot(pg_store.db.connection, data)
    assert pg_store.boards() == []
