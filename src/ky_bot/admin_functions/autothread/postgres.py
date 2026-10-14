"""VICE rule semantics on the shared PostgreSQL connection, with isolated tables."""

import re

from ky_bot.admin_functions.autothread.store import RuleStore
from ky_bot.storage.postgres import (
    ChecklistConnection,
    PostgresChecklistStore,
    mutation,
    read_operation,
)


class RuleConnection(ChecklistConnection):
    def execute(self, statement, parameters=()):
        for table in ("rules", "processed", "notices", "retired_rules", "welcome_deliveries"):
            statement = re.sub(r"\b" + table + r"\b", "autothread_" + table, statement)
        statement = statement.replace("embed_color IS ?", "embed_color IS NOT DISTINCT FROM ?")
        if "INSERT OR REPLACE INTO autothread_notices" in statement:
            statement = statement.replace("INSERT OR REPLACE", "INSERT")
            statement += (
                " ON CONFLICT(message) DO UPDATE SET "
                "channel=excluded.channel,expires=excluded.expires"
            )
        return super().execute(statement, parameters)


class PostgresRuleStore(RuleStore):
    def __init__(self, database_url):
        base = PostgresChecklistStore(database_url)
        self.db = RuleConnection(base.db.connection)
        self.operation_lock = base.operation_lock


for name in (
    "save",
    "save_configuration",
    "delete",
    "welcome",
    "claim",
    "finish",
    "retry",
    "notice",
    "forget_notice",
    "toggle",
    "initialize_colors",
):
    setattr(PostgresRuleStore, name, mutation(getattr(RuleStore, name)))
for name in ("rule", "job", "pending", "archive_rules", "expired_notices", "list"):
    setattr(PostgresRuleStore, name, read_operation(getattr(RuleStore, name)))
