"""Durable checklist tasks, channel setup and completion-log outbox."""

import json
import sqlite3
import time
from pathlib import Path


class ChecklistStore:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS boards (
                channel INTEGER PRIMARY KEY, guild INTEGER NOT NULL, log_channel INTEGER NOT NULL,
                message INTEGER, page INTEGER NOT NULL DEFAULT 0, dirty INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, channel INTEGER NOT NULL,
                source INTEGER NOT NULL UNIQUE, author INTEGER NOT NULL, title TEXT NOT NULL,
                attachments TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0,
                revision INTEGER NOT NULL DEFAULT 0, thread INTEGER,
                seeded INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT, channel INTEGER NOT NULL,
                task INTEGER NOT NULL, title TEXT NOT NULL, actor INTEGER NOT NULL,
                created REAL NOT NULL, sent INTEGER NOT NULL DEFAULT 0);
        """)
        task_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(tasks)")}
        log_columns = {row["name"] for row in self.db.execute("PRAGMA table_info(logs)")}
        if "log_channel" not in log_columns:
            with self.db:
                self.db.execute(
                    "ALTER TABLE logs ADD COLUMN log_channel INTEGER NOT NULL DEFAULT 0"
                )
        if "source_deleted" not in task_columns:
            with self.db:
                self.db.execute(
                    "ALTER TABLE tasks ADD COLUMN source_deleted INTEGER NOT NULL DEFAULT 0"
                )
        with self.db:
            self.db.execute(
                "CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY AUTOINCR"
                "EMENT, channel INTEGER NOT NULL, name TEXT NOT NULL, name_key TEXT NOT"
                " NULL, UNIQUE(channel,name_key))"
            )
            for column, definition in [
                ("category_id", "INTEGER"),
                ("awaiting_category", "INTEGER NOT NULL DEFAULT 0"),
                ("category_prompt", "INTEGER"),
            ]:
                if column not in task_columns:
                    self.db.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")
        with self.db:
            if "deleted" not in task_columns:
                self.db.execute("ALTER TABLE tasks ADD COLUMN deleted INTEGER NOT NULL DEFAULT 0")
            if "deleted_by" not in task_columns:
                self.db.execute("ALTER TABLE tasks ADD COLUMN deleted_by INTEGER")
        with self.db:
            for column, definition in [
                ("priority", "TEXT NOT NULL DEFAULT 'medium'"),
                ("position", "INTEGER NOT NULL DEFAULT 0"),
                ("source_title", "TEXT"),
            ]:
                if column not in task_columns:
                    self.db.execute(f"ALTER TABLE tasks ADD COLUMN {column} {definition}")
                    if column == "position":
                        self.db.execute("UPDATE tasks SET position=id")
                    if column == "source_title":
                        self.db.execute("UPDATE tasks SET source_title=title")
            category_columns = {r["name"] for r in self.db.execute("PRAGMA table_info(categories)")}
            if "position" not in category_columns:
                self.db.execute(
                    "ALTER TABLE categories ADD COLUMN position INTEGER NOT NULL DEFAULT 0"
                )
                self.db.execute("UPDATE categories SET position=id")
            if "revision" not in category_columns:
                self.db.execute(
                    "ALTER TABLE categories ADD COLUMN revision INTEGER NOT NULL DEFAULT 0"
                )
        with self.db:
            if "thread_sync" not in task_columns:
                self.db.execute(
                    "ALTER TABLE tasks ADD COLUMN thread_sync INTEGER NOT NULL DEFAULT 0"
                )
            self.db.execute(
                "UPDATE tasks SET thread_sync=1 WHERE done=1 AND thread IS NOT NULL AND deleted=0"
            )
        columns = {row["name"] for row in self.db.execute("PRAGMA table_info(boards)")}
        for column, definition in [
            ("name", "TEXT NOT NULL DEFAULT 'ADMIN CHECKLIST'"),
            ("style", "TEXT NOT NULL DEFAULT 'gold'"),
            ("height", "INTEGER NOT NULL DEFAULT 280"),
            ("footer_text", "TEXT"),
            ("role_id", "INTEGER"),
            ("header_background", "BLOB"),
            ("footer_background", "BLOB"),
            ("footer_icon", "BLOB"),
        ]:
            if column not in columns:
                with self.db:
                    self.db.execute(f"ALTER TABLE boards ADD COLUMN {column} {definition}")

        if "task_view" not in {row[1] for row in self.db.execute("PRAGMA table_info(boards)")}:
            with self.db:
                self.db.execute(
                    "ALTER TABLE boards ADD COLUMN task_view TEXT NOT NULL DEFAULT 'open'"
                )
                self.db.execute("UPDATE boards SET dirty=1")

    def pending_cleanup(self):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM tasks WHERE source_deleted=0 AND awaiting_category=0 AND"
                " deleted=0 ORDER BY id"
            )
        ]

    def cleaned(self, tid):
        with self.db:
            self.db.execute("UPDATE tasks SET source_deleted=1 WHERE id=?", (tid,))

    def boards(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM boards")]

    def board(self, channel):
        row = self.db.execute("SELECT * FROM boards WHERE channel=?", (channel,)).fetchone()
        return dict(row) if row else None

    def create(self, channel, guild, log_channel, name=None):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO boards(channel,guild,log_channel,name) VALUES (?,?,?,?)",
                (channel, guild, log_channel, name or "ADMIN CHECKLIST"),
            )

    def update_board(self, channel, **values):
        assert set(values) <= {
            "message",
            "page",
            "dirty",
            "name",
            "task_view",
            "style",
            "height",
            "footer_text",
            "role_id",
            "header_background",
            "footer_background",
            "footer_icon",
            "log_channel",
        }
        with self.db:
            self.db.execute(
                "UPDATE boards SET " + ",".join(f"{k}=?" for k in values) + " WHERE channel=?",
                (*values.values(), channel),
            )

    def add(self, channel, source, author, title, attachments):
        with self.db:
            cursor = self.db.execute(
                (
                    "INSERT OR IGNORE INTO tasks(channel,source,author,title,attachments,aw"
                    "aiting_category,source_title,position) VALUES (?,?,?,?,?,?,?,?)"
                ),
                (
                    channel,
                    source,
                    author,
                    title,
                    json.dumps(attachments),
                    1,
                    title,
                    self.next_position(channel),
                ),
            )
            if cursor.rowcount:
                self.db.execute("UPDATE boards SET dirty=1 WHERE channel=?", (channel,))
            return bool(cursor.rowcount)

    def tasks(self, channel):
        return [
            dict(r)
            for r in self.db.execute(
                (
                    "SELECT * FROM tasks WHERE channel=? AND awaiting_category=0 AND delete"
                    "d=0 ORDER BY done,position,id"
                ),
                (channel,),
            )
        ]

    def task(self, tid):
        row = self.db.execute("SELECT * FROM tasks WHERE id=? AND deleted=0", (tid,)).fetchone()
        if not row:
            raise ValueError("This task is no longer available.")
        return dict(row)

    def set_done(self, tid, actor, revision, done):
        item = self.task(tid)
        if item["revision"] != revision or bool(item["done"]) == done:
            raise ValueError("This task changed. Select it again for its current controls.")
        with self.db:
            self.db.execute(
                "UPDATE tasks SET done=?,thread_sync=1,revision=revision+1 WHERE id=?",
                (int(done), tid),
            )
            self.db.execute("UPDATE boards SET dirty=1 WHERE channel=?", (item["channel"],))
            if done:
                self.db.execute(
                    "INSERT INTO logs(channel,task,title,actor,created,log_channel) "
                    "VALUES (?,?,?,?,?,?)",
                    (
                        item["channel"],
                        tid,
                        item["title"],
                        actor,
                        time.time(),
                        self.board(item["channel"])["log_channel"],
                    ),
                )

    def thread(self, tid, thread_id, seeded=False):
        with self.db:
            self.db.execute(
                "UPDATE tasks SET thread=?,seeded=? WHERE id=?", (thread_id, int(seeded), tid)
            )

    def pending_logs(self):
        return [dict(r) for r in self.db.execute("SELECT * FROM logs WHERE sent=0 ORDER BY id")]

    def sent(self, lid):
        with self.db:
            self.db.execute("UPDATE logs SET sent=1 WHERE id=?", (lid,))

    def categories(self, channel):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM categories WHERE channel=? ORDER BY position,id", (channel,)
            )
        ]

    def add_category(self, channel, name):
        name = " ".join(name.split())
        if not 1 <= len(name) <= 50:
            raise ValueError("Category names must be between 1 and 50 characters.")
        if name.casefold() == "uncategorized":
            raise ValueError("Uncategorized already exists for tasks without a category.")
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO categories(channel,name,name_key,position) VALUES (?,?,?,?)",
                (channel, name, name.casefold(), self.next_category_position(channel)),
            )
            self.db.execute("UPDATE boards SET dirty=1 WHERE channel=?", (channel,))
        return self.db.execute(
            "SELECT id FROM categories WHERE channel=? AND name_key=?", (channel, name.casefold())
        ).fetchone()[0]

    def move_task(self, tid, category, revision, priority=None):
        task = self.task(tid)
        if task["revision"] != revision:
            raise ValueError("This task changed. Open its current category controls again.")
        if (
            category is not None
            and not self.db.execute(
                "SELECT id FROM categories WHERE id=? AND channel=?", (category, task["channel"])
            ).fetchone()
        ):
            raise ValueError("Choose a category from this checklist.")
        if priority is not None and priority not in ("high", "medium", "low"):
            raise ValueError("Choose High, Medium, or Low.")
        with self.db:
            self.db.execute(
                (
                    "UPDATE tasks SET category_id=?,priority=?,awaiting_category=0,position"
                    "=?,revision=revision+1 WHERE id=?"
                ),
                (category, priority or task["priority"], self.next_position(task["channel"]), tid),
            )
            self.db.execute("UPDATE boards SET dirty=1 WHERE channel=?", (task["channel"],))

    def category_tasks(self):
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT * FROM tasks WHERE (awaiting_category=1 AND deleted=0) OR categ"
                "ory_prompt IS NOT NULL ORDER BY id"
            )
        ]

    def prompt(self, tid, mid):
        with self.db:
            self.db.execute("UPDATE tasks SET category_prompt=? WHERE id=?", (mid, tid))

    def delete_task(self, tid, actor, revision):
        task = self.task(tid)
        if task["revision"] != revision:
            raise ValueError("This task changed. Select it again before deleting it.")
        with self.db:
            self.db.execute(
                "UPDATE tasks SET deleted=1,deleted_by=?,revision=revision+1 WHERE id=?",
                (actor, tid),
            )
            self.db.execute("UPDATE boards SET dirty=1 WHERE channel=?", (task["channel"],))

    def next_position(self, channel):
        return self.db.execute(
            "SELECT COALESCE(MAX(position),0)+1 FROM tasks WHERE channel=?", (channel,)
        ).fetchone()[0]

    def next_category_position(self, channel):
        return self.db.execute(
            "SELECT COALESCE(MAX(position),0)+1 FROM categories WHERE channel=?", (channel,)
        ).fetchone()[0]

    def edit_task(self, tid, revision, title=None, priority=None):
        task = self.task(tid)
        if task["revision"] != revision:
            raise ValueError("This task changed. Open its controls again.")
        if title is not None:
            title = title.strip()
            if not 1 <= len(title) <= 4000:
                raise ValueError("Task text must be 1–4000 characters.")
        if priority is not None and priority not in ("high", "medium", "low"):
            raise ValueError("Choose High, Medium, or Low.")
        with self.db:
            self.db.execute(
                "UPDATE tasks SET title=?,priority=?,revision=revision+1 WHERE id=?",
                (task["title"] if title is None else title, priority or task["priority"], tid),
            )
            self.db.execute("UPDATE boards SET dirty=1 WHERE channel=?", (task["channel"],))

    def category(self, cid, channel):
        row = self.db.execute(
            "SELECT * FROM categories WHERE id=? AND channel=?", (cid, channel)
        ).fetchone()
        if row is None:
            raise ValueError("This category is no longer available.")
        return dict(row)

    def rename_category(self, cid, channel, revision, name):
        category = self.category(cid, channel)
        if category["revision"] != revision:
            raise ValueError("This category changed. Open its controls again.")
        name = " ".join(name.split())
        if not 1 <= len(name) <= 50 or name.casefold() == "uncategorized":
            raise ValueError("Use 1–50 characters; Uncategorized is reserved.")
        if self.db.execute(
            "SELECT id FROM categories WHERE channel=? AND name_key=? AND id<>?",
            (channel, name.casefold(), cid),
        ).fetchone():
            raise ValueError("A category with that name already exists.")
        with self.db:
            self.db.execute(
                "UPDATE categories SET name=?,name_key=?,revision=revision+1 WHERE id=?",
                (name, name.casefold(), cid),
            )
            self.db.execute("UPDATE boards SET dirty=1 WHERE channel=?", (channel,))

    def reorder(self, channel, kind, ident, action, revision):
        if kind == "task":
            item = self.task(ident)
            if item["channel"] != channel:
                raise ValueError("Use this task’s own checklist.")
            items = [
                t
                for t in self.tasks(channel)
                if t["category_id"] == item["category_id"]
                and (action == "priority" or t["done"] == item["done"])
            ]
            table = "tasks"
        elif kind == "category":
            item = self.category(ident, channel)
            items = self.categories(channel)
            table = "categories"
        else:
            raise ValueError("Unknown list type.")
        if item["revision"] != revision:
            raise ValueError("This item changed. Open its controls again.")
        ids = [t["id"] for t in items]
        if ident not in ids:
            raise ValueError("File this task before ordering it.")
        index = ids.index(ident)
        if action == "priority" and kind == "task":
            items.sort(key=lambda t: (t["done"], {"high": 0, "medium": 1, "low": 2}[t["priority"]]))
            ids = [t["id"] for t in items]
        else:
            target = {
                "up": max(0, index - 1),
                "down": min(len(ids) - 1, index + 1),
                "top": 0,
                "bottom": len(ids) - 1,
            }.get(action)
            if target is None:
                raise ValueError("Choose a move action.")
            ids.insert(target, ids.pop(index))
        with self.db:
            for position, item_id in enumerate(ids, 1):
                self.db.execute(
                    f"UPDATE {table} SET position=?,revision=revision+1 WHERE id=?",
                    (position, item_id),
                )
            self.db.execute("UPDATE boards SET dirty=1 WHERE channel=?", (channel,))

    def pending_thread_sync(self):
        return [
            dict(r)
            for r in self.db.execute("SELECT * FROM tasks WHERE thread_sync=1 AND deleted=0")
        ]

    def thread_synced(self, tid):
        with self.db:
            self.db.execute("UPDATE tasks SET thread_sync=0 WHERE id=?", (tid,))
