"""Подключение к базе и применение миграций.

Миграции — обычные .sql файлы, применяемые по порядку имён. Alembic появится,
когда схема начнёт меняться часто; пока прозрачный SQL читается лучше, чем
сгенерированный питон, и его можно открыть глазами в psql.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
MIGRATION_PATTERN = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")


class DatabaseError(RuntimeError):
    pass


def database_url() -> str:
    url = os.environ.get("SAIA_DATABASE_URL")
    if not url:
        raise DatabaseError(
            "не задан SAIA_DATABASE_URL.\n"
            "  В Docker он подставляется автоматически.\n"
            "  Вне Docker: export SAIA_DATABASE_URL=postgresql://saia:saia@localhost:5434/saia"
        )
    return url


def connect() -> psycopg.Connection:
    return psycopg.connect(database_url())


def applied_versions(conn: psycopg.Connection) -> set[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
        )
        exists = cur.fetchone()[0]
        if not exists:
            return set()
        cur.execute("SELECT version FROM schema_migrations")
        return {row[0] for row in cur.fetchall()}


def pending(conn: psycopg.Connection) -> list[Path]:
    done = applied_versions(conn)
    files = sorted(p for p in MIGRATIONS_DIR.glob("*.sql") if MIGRATION_PATTERN.match(p.name))
    return [p for p in files if p.stem not in done]


def migrate(verbose: bool = True) -> list[str]:
    """Применить непринятые миграции. Каждая — в своей транзакции."""
    applied: list[str] = []
    with connect() as conn:
        for path in pending(conn):
            if verbose:
                print(f"применяю {path.name} ...", flush=True)
            try:
                with conn.transaction():
                    conn.execute(path.read_text(encoding="utf-8"))
            except psycopg.Error as error:
                raise DatabaseError(f"миграция {path.name} не применилась: {error}") from error
            applied.append(path.stem)
    if verbose:
        print("нечего применять" if not applied else f"применено: {', '.join(applied)}")
    return applied


def status() -> str:
    with connect() as conn:
        done = sorted(applied_versions(conn))
        waiting = [p.stem for p in pending(conn)]
    lines = [f"База: {database_url().rsplit('@', 1)[-1]}", ""]
    lines.append("Применённые миграции:")
    lines += [f"  {v}" for v in done] or ["  —"]
    lines.append("")
    lines.append("Ожидают применения:")
    lines += [f"  {v}" for v in waiting] or ["  —"]
    return "\n".join(lines)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Миграции базы Horizon")
    parser.add_argument("command", choices=["migrate", "status"], nargs="?", default="migrate")
    args = parser.parse_args()

    if args.command == "status":
        print(status())
    else:
        migrate()


if __name__ == "__main__":
    main()
