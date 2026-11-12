from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

APP_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = APP_ROOT / "migrations"

SQLITE_SCHEMA = """
create table if not exists schema_migrations(version integer primary key);
create table if not exists members(
  id integer primary key autoincrement,
  key text unique not null,
  display_name text not null,
  role text not null check (role in ('owner','member')),
  telegram_chat_id integer unique,
  discord_user_id text unique,
  link_code text unique,
  preferred_channel text check (preferred_channel in ('telegram','discord'))
);
create table if not exists tasks(
  id integer primary key autoincrement,
  title text not null,
  details text,
  assignee_id integer not null references members(id),
  created_by integer not null references members(id),
  due_at text,
  status text not null default 'open' check (status in ('open','done','cancelled')),
  done_at text,
  reminded_at text,
  overdue_notified_on text,
  created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
create table if not exists processed_events(
  source text not null,
  event_id text not null,
  at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  primary key(source, event_id)
);
create table if not exists audit_log(
  id integer primary key autoincrement,
  at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  member_id integer,
  action text not null,
  payload text not null
);
"""


def get_db_url() -> str:
    return os.getenv("DATABASE_URL") or "sqlite:///./command_hub.db"


def is_sqlite() -> bool:
    return get_db_url().startswith("sqlite:")


def get_connection() -> sqlite3.Connection | psycopg.Connection:
    url = get_db_url()
    if url.startswith("sqlite:"):
        path = url.removeprefix("sqlite:///")
        connection = sqlite3.connect(path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection
    return psycopg.connect(url, row_factory=dict_row)


def sql_for_connection(sql: str) -> str:
    return sql if is_sqlite() else sql.replace("?", "%s")


def apply_migrations() -> None:
    connection = get_connection()
    try:
        if is_sqlite():
            connection.executescript(SQLITE_SCHEMA)
            connection.execute(
                "insert or ignore into schema_migrations(version) values (1)"
            )
            for version, migration_name in (
                (2, "0002_task_approval.sql"),
                (3, "0003_discord_task_notifications.sql"),
            ):
                migration = connection.execute(
                    "select 1 from schema_migrations where version = ?", (version,)
                ).fetchone()
                if migration is not None:
                    continue
                migration_sql = (MIGRATIONS_DIR / migration_name).read_text()
                migration_sql = migration_sql.replace(
                    "default now()",
                    "default (strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                )
                for statement in migration_sql.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(
                    "insert into schema_migrations(version) values (?)", (version,)
                )
            connection.commit()
            return

        connection.execute("select pg_advisory_lock(431721091)")
        connection.execute(
            "create table if not exists schema_migrations(version integer primary key)"
        )
        for version in (1, 2, 3):
            exists = connection.execute(
                "select 1 from schema_migrations where version = %s", (version,)
            ).fetchone()
            if exists is not None:
                continue
            migration_path = MIGRATIONS_DIR / f"{version:04d}_init.sql"
            if version == 2:
                migration_path = MIGRATIONS_DIR / "0002_task_approval.sql"
            elif version == 3:
                migration_path = MIGRATIONS_DIR / "0003_discord_task_notifications.sql"
            for statement in migration_path.read_text().split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                "insert into schema_migrations(version) values (%s)", (version,)
            )
        connection.commit()
        connection.execute("select pg_advisory_unlock(431721091)")
    finally:
        connection.close()


def fetchone(sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    connection = get_connection()
    try:
        row = connection.execute(sql_for_connection(sql), params).fetchone()
        return dict(row) if row is not None else None
    finally:
        connection.close()


def fetchall(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    connection = get_connection()
    try:
        return [
            dict(row)
            for row in connection.execute(sql_for_connection(sql), params).fetchall()
        ]
    finally:
        connection.close()


def execute(sql: str, params: tuple[Any, ...] = ()) -> int:
    connection = get_connection()
    try:
        cursor = connection.execute(sql_for_connection(sql), params)
        result = cursor.rowcount
        connection.commit()
        return result
    finally:
        connection.close()


def ensure_seed_members() -> None:
    owner_chat_id_value = os.getenv("OWNER_TELEGRAM_CHAT_ID")
    owner_chat_id = int(owner_chat_id_value) if owner_chat_id_value else None
    owner = fetchone("select id from members where key = ?", ("owner",))
    if owner is None:
        execute(
            """insert into members(key, display_name, role, telegram_chat_id,
                                   preferred_channel)
               values (?, ?, 'owner', ?, 'telegram')""",
            ("owner", os.getenv("OWNER_DISPLAY_NAME", "Administrator"), owner_chat_id),
        )
    elif owner_chat_id:
        execute(
            "update members set telegram_chat_id = ? where key = ?",
            (owner_chat_id, "owner"),
        )

    roster = json.loads(os.getenv("TEAM_ROSTER", "[]"))
    if not isinstance(roster, list):
        raise TypeError("TEAM_ROSTER must be a JSON array.")
    for member in roster:
        key = member.get("key")
        display_name = member.get("display_name")
        if not key or not display_name:
            raise ValueError("Each TEAM_ROSTER entry needs key and display_name.")
        values = (
            display_name,
            member.get("telegram_chat_id"),
            member.get("discord_user_id"),
            member.get("preferred_channel", "telegram"),
            key,
        )
        existing = fetchone("select id from members where key = ?", (key,))
        if existing:
            execute(
                """update members set display_name = ?,
                       telegram_chat_id = coalesce(?, telegram_chat_id),
                       discord_user_id = coalesce(?, discord_user_id),
                       preferred_channel = ? where key = ?""",
                values,
            )
        else:
            execute(
                """insert into members(key, display_name, role, telegram_chat_id,
                         discord_user_id, preferred_channel)
                   values (?, ?, 'member', ?, ?, ?)""",
                (
                    key,
                    display_name,
                    member.get("telegram_chat_id"),
                    member.get("discord_user_id"),
                    member.get("preferred_channel", "telegram"),
                ),
            )


def roster() -> dict[str, str]:
    return {
        member["key"]: member["display_name"]
        for member in fetchall("select key, display_name from members order by key")
    }


def get_member_by_key(key: str) -> dict[str, Any] | None:
    return fetchone("select * from members where key = ?", (key,))


def get_member_by_id(member_id: int) -> dict[str, Any] | None:
    return fetchone("select * from members where id = ?", (member_id,))


def member_from_chat(platform: str, chat_id: int | str) -> dict[str, Any] | None:
    column = "telegram_chat_id" if platform == "telegram" else "discord_user_id"
    return fetchone(f"select * from members where {column} = ?", (str(chat_id),))


def link_member(
    platform: str, account_id: int | str, code: str, display_name: str
) -> dict[str, Any] | None:
    link_codes = json.loads(os.getenv("LINK_CODES", "{}"))
    configured = link_codes.get(code)
    if not isinstance(configured, dict):
        return None
    key = configured.get("key")
    if not key:
        return None
    column = "telegram_chat_id" if platform == "telegram" else "discord_user_id"
    connection = get_connection()
    try:
        if is_sqlite():
            connection.execute("begin immediate")
        else:
            connection.execute("select pg_advisory_xact_lock(431721093)")
        used = connection.execute(
            sql_for_connection("select 1 from used_link_codes where code = ?"),
            (code,),
        ).fetchone()
        if used is not None:
            connection.rollback()
            return None
        member = connection.execute(
            sql_for_connection("select * from members where key = ?"), (key,)
        ).fetchone()
        if member is not None and member[column] is not None:
            connection.rollback()
            return None
        if member is None:
            connection.execute(
                sql_for_connection(
                    """insert into members(key, display_name, role, telegram_chat_id,
                                           discord_user_id, preferred_channel)
                       values (?, ?, 'member', ?, ?, ?)"""
                ),
                (
                    key,
                    configured.get("display_name", display_name),
                    account_id if platform == "telegram" else None,
                    str(account_id) if platform == "discord" else None,
                    platform,
                ),
            )
        else:
            connection.execute(
                sql_for_connection(f"update members set {column} = ? where key = ?"),
                (account_id, key),
            )
        connection.execute(
            sql_for_connection(
                "insert into used_link_codes(code, member_key, platform) values (?, ?, ?)"
            ),
            (code, key, platform),
        )
        connection.commit()
    finally:
        connection.close()
    return member_from_chat(platform, account_id)


def record_event(source: str, event_id: str) -> bool:
    try:
        execute(
            "insert into processed_events(source, event_id) values (?, ?)",
            (source, event_id),
        )
        return True
    except (sqlite3.IntegrityError, psycopg.errors.UniqueViolation):
        return False


def is_duplicate_event(source: str, event_id: str) -> bool:
    return fetchone(
        "select 1 as found from processed_events where source = ? and event_id = ?",
        (source, event_id),
    ) is not None


def log_action(member_id: int | None, action: str, payload: dict[str, Any]) -> None:
    execute(
        "insert into audit_log(member_id, action, payload) values (?, ?, ?)",
        (member_id, action, json.dumps(payload, ensure_ascii=False, default=str)),
    )
