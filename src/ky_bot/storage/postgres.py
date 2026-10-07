"""PostgreSQL adapters preserving the existing repository interfaces.

Web backends should use these repositories rather than writing checklist tables
directly: checklist mutations serialize their read/check/write transaction.
"""

from contextlib import asynccontextmanager
from functools import wraps
from pathlib import Path
from threading import RLock

from ky_bot.admin_functions.settings_repository import SettingsRepository
from ky_bot.checklists.store import ChecklistStore

SCHEMA = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
CHECKLIST_LOCK = 1264141135


def postgres_sql(statement):
    """Translate only our parameterized repository SQL, never user-supplied SQL."""
    statement = statement.replace("?", "%s")
    if "INSERT OR IGNORE INTO" in statement:
        statement = statement.replace("INSERT OR IGNORE INTO", "INSERT INTO")
        statement += " ON CONFLICT DO NOTHING"
    return statement


class Record(dict):
    """Support both named columns and SQLite-style positional row access."""

    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


def record_row(cursor):
    columns = [column.name for column in cursor.description] if cursor.description else []
    return lambda values: Record(zip(columns, values))


class ChecklistConnection:
    def __init__(self, connection):
        self.connection = connection
        self.transactions = []

    def execute(self, statement, parameters=()):
        return self.connection.execute(postgres_sql(statement), parameters)

    def __enter__(self):
        transaction = self.connection.transaction()
        transaction.__enter__()
        self.transactions.append(transaction)
        return self

    def __exit__(self, *error):
        return self.transactions.pop().__exit__(*error)

    def close(self):
        self.connection.close()


def mutation(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        # Acquire before reading the revision, not just before the UPDATE.
        with self.operation_lock:
            with self.db:
                self.db.execute("SELECT pg_advisory_xact_lock(?)", (CHECKLIST_LOCK,))
                return method(self, *args, **kwargs)

    return run


class PostgresChecklistStore(ChecklistStore):
    def __init__(self, database_url):
        import psycopg

        self.operation_lock = RLock()
        connection = psycopg.connect(
            database_url,
            autocommit=True,
            connect_timeout=10,
            options="-c statement_timeout=5000 -c lock_timeout=5000",
            row_factory=record_row,
        )
        self.db = ChecklistConnection(connection)
        try:
            with self.db:
                self.db.execute("SELECT pg_advisory_xact_lock(?)", (CHECKLIST_LOCK,))
                connection.execute(SCHEMA, prepare=False)
        except BaseException:
            connection.close()
            raise


for _name in (
    "create",
    "update_board",
    "add",
    "set_done",
    "thread",
    "sent",
    "add_category",
    "move_task",
    "prompt",
    "delete_task",
    "edit_task",
    "rename_category",
    "reorder",
    "cleaned",
    "thread_synced",
    "rendered",
):
    setattr(PostgresChecklistStore, _name, mutation(getattr(ChecklistStore, _name)))


def read_operation(method):
    @wraps(method)
    def run(self, *args, **kwargs):
        with self.operation_lock:
            return method(self, *args, **kwargs)

    return run


for _name in (
    "boards",
    "board",
    "tasks",
    "task",
    "categories",
    "category",
    "category_tasks",
    "pending_logs",
    "pending_cleanup",
    "pending_thread_sync",
    "next_position",
    "next_category_position",
    "is_checklist_channel",
):
    setattr(PostgresChecklistStore, _name, read_operation(getattr(ChecklistStore, _name)))


class AsyncExecution:
    """The awaitable/context-manager execute interface used by SettingsRepository."""

    def __init__(self, connection, statement, parameters):
        self.connection, self.statement, self.parameters = connection, statement, parameters
        self.cursor = None

    async def run(self):
        if self.cursor is None:
            self.cursor = await self.connection.execute(
                postgres_sql(self.statement), self.parameters
            )
        return self.cursor

    def __await__(self):
        return self.run().__await__()

    async def __aenter__(self):
        return await self.run()

    async def __aexit__(self, *_):
        await self.cursor.close()


class AsyncSettingsConnection:
    def __init__(self, connection):
        self.connection = connection

    def execute(self, statement, parameters=()):
        return AsyncExecution(self.connection, statement, parameters)


@asynccontextmanager
async def open_postgres_settings(database_url):
    import psycopg

    connection = await psycopg.AsyncConnection.connect(
        database_url,
        autocommit=True,
        connect_timeout=10,
        options="-c statement_timeout=5000 -c lock_timeout=5000",
    )
    try:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock(%s)", (CHECKLIST_LOCK,))
            await connection.execute(SCHEMA, prepare=False)
        yield SettingsRepository(AsyncSettingsConnection(connection))
    finally:
        await connection.close()
