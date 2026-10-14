"""Isolated AutoReact tables on the shared PostgreSQL database."""

import re

from ky_bot.storage.postgres import (
    ChecklistConnection,
    PostgresChecklistStore,
    mutation,
    read_operation,
)

from .store import Store


class Connection(ChecklistConnection):
    def execute(self, statement, parameters=()):
        for table in ("rules", "jobs"):
            statement = re.sub(r"\b" + table + r"\b", "autoreact_" + table, statement)
        if statement == "BEGIN IMMEDIATE":
            return None
        if "INSERT OR REPLACE INTO autoreact_rules" in statement:
            statement = statement.replace("INSERT OR REPLACE", "INSERT")
            statement += (
                " ON CONFLICT(guild,channel) DO UPDATE SET settings=excluded.settings,"
                "enabled=excluded.enabled,revision=excluded.revision,starts=excluded.starts,error=excluded.error"
            )
        return super().execute(statement, parameters)


class PostgresStore(Store):
    def __init__(self, database_url):
        base = PostgresChecklistStore(database_url)
        self.db = Connection(base.db.connection)
        self.operation_lock = base.operation_lock


for name in ("save", "action", "enqueue", "update_job", "error", "prune"):
    setattr(PostgresStore, name, mutation(getattr(Store, name)))
for name in ("list", "get", "jobs"):
    setattr(PostgresStore, name, read_operation(getattr(Store, name)))
