"""Shared PostgreSQL storage for VICE cleanup and opt-in activity logs."""

import re
import sqlite3

from ky_bot.storage.postgres import (
    CHECKLIST_LOCK,
    ChecklistConnection,
    PostgresChecklistStore,
    mutation,
    read_operation,
)

from .autodelete_store import RuleStore

TABLES = [
    "rules",
    "thread_sources",
    "activity_log_links",
    "activity_log_batches",
    "activity_log_events",
    "activity_log_retention",
    "activity_log_config",
    "activity_log_reactions",
    "activity_log_deletions",
    "activity_log_seed",
    "activity_log_messages",
    "activity_log_queue",
]
UPSERTS = {
    "thread_sources": ("guild,thread", "kinds"),
    "activity_log_config": ("guild,source", "destination,title,color,types,enabled"),
    "activity_log_links": ("guild,message", "root,context"),
    "activity_log_messages": ("guild,message", "channel,data"),
    "activity_log_deletions": ("guild,message", "status"),
}


class Connection(ChecklistConnection):
    def execute(self, sql, parameters=()):
        import psycopg

        original = sql
        for table in TABLES:
            sql = re.sub(r"\b" + table + r"\b", "autodelete_" + table, sql)
        if "INSERT OR REPLACE" in sql:
            table = next(t for t in UPSERTS if f"INTO {t} " in original)
            keys, columns = UPSERTS[table]
            sql = sql.replace("INSERT OR REPLACE", "INSERT")
            sql += (
                " ON CONFLICT("
                + keys
                + ") DO UPDATE SET "
                + ",".join(f"{c}=excluded.{c}" for c in columns.split(","))
            )
        try:
            return super().execute(sql, parameters)
        except psycopg.IntegrityError as error:
            raise sqlite3.IntegrityError(str(error)) from error

    def executescript(self, sql):
        self.execute("SELECT pg_advisory_xact_lock(?)", (CHECKLIST_LOCK,))
        sql = re.sub(r"\bINTEGER\b", "BIGINT", sql)
        sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)
        for statement in sql.split(";"):
            if statement.strip():
                self.execute(statement)


class PostgresStore(RuleStore):
    def __init__(self, database_url):
        base = PostgresChecklistStore(database_url)
        self.db = Connection(base.db.connection)
        self.operation_lock = base.operation_lock
        with self.db:
            self.db.execute("SELECT pg_advisory_xact_lock(?)", (CHECKLIST_LOCK,))
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS rules (
                    id BIGSERIAL PRIMARY KEY, guild BIGINT NOT NULL,channel BIGINT NOT NULL,
                    content TEXT NOT NULL,seconds BIGINT NOT NULL,timeframe TEXT NOT NULL,
                    enabled BIGINT NOT NULL DEFAULT 0,actor BIGINT NOT NULL,
                    starts_at DOUBLE PRECISION NOT NULL DEFAULT 0,
                    UNIQUE(guild,channel,content,seconds))
            """)
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS thread_sources (
                    guild BIGINT NOT NULL,thread BIGINT NOT NULL,kinds TEXT NOT NULL,
                    PRIMARY KEY(guild,thread))
            """)


for name in ("save", "update_rule", "delete", "toggle", "remember_thread", "forget_thread"):
    setattr(PostgresStore, name, mutation(getattr(RuleStore, name)))
for name in ("list", "enabled", "thread_kinds"):
    setattr(PostgresStore, name, read_operation(getattr(RuleStore, name)))
