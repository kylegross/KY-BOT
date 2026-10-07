"""Explicit, atomic SQLite-to-PostgreSQL import; source files are read-only."""

import argparse
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from dotenv import load_dotenv

from ky_bot.storage.postgres import CHECKLIST_LOCK, PostgresChecklistStore

SETTINGS_TABLES = ("guild_settings", "welcome_settings", "welcome_artwork")
CHECKLIST_TABLES = ("boards", "categories", "tasks", "logs")
TABLES = SETTINGS_TABLES + CHECKLIST_TABLES


def snapshot(path, tables):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError(f"Source database does not exist: {path.name}")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
        source.execute("BEGIN")
        existing = {
            row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if not set(tables) <= existing:
            raise ValueError("Source database needs the current KY BOT SQLite schema.")
        return {
            table: (
                [column[1] for column in source.execute(f"PRAGMA table_info({table})")],
                source.execute(f"SELECT * FROM {table}").fetchall(),
            )
            for table in tables
        }


def import_snapshot(connection, data):
    """Refuse populated targets and preserve every source primary key in one commit."""
    from psycopg import sql

    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(%s)", (CHECKLIST_LOCK,))
        connection.execute("LOCK TABLE " + ", ".join(TABLES) + " IN EXCLUSIVE MODE")
        for table in TABLES:
            if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                raise ValueError("Target is not empty; import refused to avoid overwriting data.")
        for table, (columns, rows) in data.items():
            statement = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                sql.Identifier(table),
                sql.SQL(",").join(map(sql.Identifier, columns)),
                sql.SQL(",").join(sql.Placeholder() for _ in columns),
            )
            with connection.cursor() as cursor:
                cursor.executemany(statement, rows)
            count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if count != len(rows):
                raise ValueError("Import verification failed; all inserted records rolled back.")
        for table in ("tasks", "categories", "logs"):
            connection.execute(
                "SELECT setval(pg_get_serial_sequence(%s, 'id'), "
                f"COALESCE(MAX(id), 1), MAX(id) IS NOT NULL) FROM {table}",
                (table,),
            )
        # Refresh Discord panels from the imported records after the bot starts.
        connection.execute("UPDATE boards SET dirty=1")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--checklists", type=Path, required=True)
    parser.add_argument(
        "--apply", action="store_true", help="Import into an empty DATABASE_URL target"
    )
    args = parser.parse_args()
    load_dotenv(override=False)
    try:
        data = snapshot(args.settings, SETTINGS_TABLES) | snapshot(
            args.checklists, CHECKLIST_TABLES
        )
        for table, (_, rows) in data.items():
            print(f"{table}: {len(rows)} records")
        if not args.apply:
            print("Preview only. No source or target data was changed.")
            return
        url = os.getenv("MIGRATION_DATABASE_URL", os.getenv("DATABASE_URL", "")).strip()
        if not url.startswith(("postgres://", "postgresql://")):
            raise ValueError("Set DATABASE_URL to the destination PostgreSQL database.")
        store = PostgresChecklistStore(url)
        try:
            import_snapshot(store.db.connection, data)
        finally:
            store.db.close()
        print("Import verified and committed. Original SQLite files were not changed.")
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    except Exception:
        # Driver connection errors can contain credentials; do not echo them.
        parser.exit(
            1, "Import failed. Check database connectivity and schema; no partial data import.\n"
        )


if __name__ == "__main__":
    main()
