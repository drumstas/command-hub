from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import psycopg
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

APP_ROOT = Path(__file__).resolve().parent.parent
MIGRATION_PATH = APP_ROOT / "migrations" / "0001_init.sql"
TIMEZONE = ZoneInfo("Europe/Vilnius")


def now_local() -> datetime:
    return datetime.now(TIMEZONE)


def get_db_url() -> str:
    return os.getenv("DATABASE_URL", "sqlite:///./command_hub.db")


def is_sqlite() -> bool:
    return get_db_url().startswith("sqlite")


def get_sqlite_path() -> str:
    url = get_db_url()
    if not url.startswith("sqlite"):
        raise RuntimeError("Expected a SQLite database URL.")
    return url.replace("sqlite:///", "", 1)


def get_connection() -> sqlite3.Connection | psycopg.Connection:
    url = get_db_url()
    if url.startswith("sqlite"):
        conn = sqlite3.connect(get_sqlite_path(), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    return psycopg.connect(url)


SQLITE_SCHEMA = """
create table if not exists members(
  id integer primary key autoincrement,
  key text unique not null,
  display_name text not null,
  role text not null check (role in ('owner','member')),
  telegram_chat_id integer unique,
  discord_user_id text unique,
  link_code text,
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

create table if not exists documents(
  id integer primary key autoincrement,
  kind text not null check (kind in ('message','quote','proforma')),
  title text not null,
  body_md text not null,
  status text not null default 'draft' check (status in ('draft','approved','void')),
  number text unique,
  created_by integer references members(id),
  approved_by integer references members(id),
  created_at text not null default (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

create table if not exists processed_events(
  source text,
  event_id text,
  at text default (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  primary key(source, event_id)
);

create table if not exists audit_log(
  id integer primary key autoincrement,
  at text default (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  member_id integer,
  action text,
  payload text
);

create table if not exists llm_calls(
  id integer primary key autoincrement,
  at text default (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  purpose text,
  model text,
  in_tokens integer,
  out_tokens integer,
  ok boolean
);
"""


def apply_migrations() -> None:
    if is_sqlite():
        conn = get_connection()
        try:
            conn.executescript(SQLITE_SCHEMA)
            conn.commit()
        finally:
            conn.close()
        return

    with psycopg.connect(get_db_url()) as conn:
        with conn.cursor() as cur:
            cur.execute(MIGRATION_PATH.read_text())
        conn.commit()


def ensure_seed_members() -> None:
    conn = get_connection()
    try:
        if is_sqlite():
            row = conn.execute(
                "select id from members where key = ?",
                (os.getenv("OWNER_CODE", "owner"),),
            ).fetchone()
            if row is None:
                conn.execute(
                    "insert into members(key, display_name, role, preferred_channel) values (?, ?, 'owner', 'telegram')",
                    (os.getenv("OWNER_CODE", "owner"), "Owner"),
                )
                conn.commit()
        else:
            cur = conn.cursor()
            cur.execute(
                "select id from members where key = %s",
                [os.getenv("OWNER_CODE", "owner")],
            )
            if cur.fetchone() is None:
                cur.execute(
                    "insert into members(key, display_name, role, preferred_channel) values (%s, %s, 'owner', 'telegram')",
                    [os.getenv("OWNER_CODE", "owner"), "Owner"],
                )
                conn.commit()
    finally:
        conn.close()


def roster() -> dict[str, str]:
    conn = get_connection()
    try:
        if is_sqlite():
            rows = conn.execute("select key, display_name from members order by key").fetchall()
        else:
            with conn.cursor() as cur:
                cur.execute("select key, display_name from members order by key")
                rows = cur.fetchall()
    finally:
        conn.close()
    return {row[0]: row[1] for row in rows}


def member_from_chat(platform: str, value: str | int) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        if is_sqlite():
            if platform == "telegram":
                row = conn.execute(
                    "select * from members where telegram_chat_id = ?",
                    (str(value),),
                ).fetchone()
            else:
                row = conn.execute(
                    "select * from members where discord_user_id = ?",
                    (str(value),),
                ).fetchone()
        else:
            with conn.cursor() as cur:
                if platform == "telegram":
                    cur.execute("select * from members where telegram_chat_id = %s", [str(value)])
                else:
                    cur.execute("select * from members where discord_user_id = %s", [str(value)])
                row = cur.fetchone()
                if row is not None:
                    row = dict(zip([desc[0] for desc in cur.description], row))
    finally:
        conn.close()
    if row is None:
        return None
    if is_sqlite():
        return dict(row)
    return row


def record_event(source: str, event_id: str) -> None:
    conn = get_connection()
    try:
        if is_sqlite():
            conn.execute(
                "insert or ignore into processed_events(source, event_id) values (?, ?)",
                (source, event_id),
            )
            conn.commit()
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into processed_events(source, event_id) values (%s, %s) on conflict do nothing",
                    [source, event_id],
                )
                conn.commit()
    finally:
        conn.close()


def is_duplicate_event(source: str, event_id: str) -> bool:
    conn = get_connection()
    try:
        if is_sqlite():
            row = conn.execute(
                "select 1 from processed_events where source = ? and event_id = ?",
                (source, event_id),
            ).fetchone()
        else:
            with conn.cursor() as cur:
                cur.execute("select 1 from processed_events where source = %s and event_id = %s", [source, event_id])
                row = cur.fetchone()
    finally:
        conn.close()
    return row is not None


def parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    candidates = [
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]
    for fmt in candidates:
        try:
            parsed = datetime.strptime(value, fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=TIMEZONE)
            return parsed.astimezone(TIMEZONE)
        except ValueError:
            continue
    return None


def parse_due_date(raw: str | None, reference: datetime | None = None) -> str | None:
    if raw is None:
        return None
    if match := parse_iso_datetime(raw):
        return match.isoformat()
    text = raw.strip().lower()
    reference = reference or now_local()
    day_map = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6,
    }
    for day_name, idx in day_map.items():
        if day_name in text:
            current = reference.weekday()
            offset = (idx - current) % 7
            if offset == 0:
                offset = 7
            due = reference + timedelta(days=offset)
            time_part = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", text)
            if time_part:
                hour = int(time_part.group(1))
                minute = int(time_part.group(2) or 0)
                if time_part.group(3) and time_part.group(3).lower() == "pm" and hour < 12:
                    hour += 12
                if time_part.group(3) and time_part.group(3).lower() == "am" and hour == 12:
                    hour = 0
                due = due.replace(hour=hour, minute=minute, second=0, microsecond=0)
            else:
                due = due.replace(hour=15, minute=0, second=0, microsecond=0)
            return due.isoformat()

    if "tomorrow" in text:
        due = (reference + timedelta(days=1)).replace(hour=15, minute=0, second=0, microsecond=0)
        return due.isoformat()
    if "today" in text:
        due = reference.replace(hour=15, minute=0, second=0, microsecond=0)
        return due.isoformat()
    return None


def parse_task_intent(text: str, roster_map: dict[str, str]) -> dict[str, Any]:
    if not text or not text.strip():
        raise ValueError("Task text is empty.")
    lowered = text.lower()
    assignee_key = None
    for key in sorted(roster_map, key=lambda item: len(item), reverse=True):
        if re.search(rf"\b{re.escape(key)}\b", lowered) or re.search(rf"\b{re.escape(key.title())}\b", text):
            assignee_key = key
            break
    if assignee_key is None:
        raise ValueError("I could not identify the assignee from the task text.")

    due_at = parse_due_date(text)
    title = text.strip()
    for key in [assignee_key, assignee_key.title()]:
        title = re.sub(rf"\b{re.escape(key)}\b", "", title, flags=re.IGNORECASE).strip(" :;,-")
    title = re.sub(r"\b(task for|for)\b", "", title, flags=re.IGNORECASE).strip(" :;,-")
    title = re.sub(r"\bby\b.*$", "", title, flags=re.IGNORECASE).strip(" :;,-")
    title = re.sub(r"\s+", " ", title).strip()
    if not title:
        raise ValueError("The task title is empty after parsing the assignee and reminder text.")

    if "by " in text.lower() and due_at is None:
        raise ValueError("I could not parse the due date. Please give a date like 'Friday 15:00' or '2026-11-15T18:00:00+02:00'.")

    return {
        "kind": "task",
        "title": title,
        "assignee_key": assignee_key,
        "due_at": due_at,
        "detail": text,
    }


def ensure_member_for_link(platform: str, value: str, code: str, display_name: str) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = None
        if is_sqlite():
            if platform == "telegram":
                row = conn.execute("select * from members where telegram_chat_id = ?", (value,)).fetchone()
            else:
                row = conn.execute("select * from members where discord_user_id = ?", (value,)).fetchone()
            if row is None:
                cursor = conn.execute(
                    "insert into members(key, display_name, role, link_code, preferred_channel, telegram_chat_id, discord_user_id) values (?, ?, 'member', ?, ?, ?, ?)",
                    (code, display_name, code, "telegram" if platform == "telegram" else "discord", value if platform == "telegram" else None, value if platform == "discord" else None),
                )
                conn.commit()
                row = conn.execute("select * from members where id = ?", (cursor.lastrowid,)).fetchone()
            else:
                conn.execute(
                    "update members set link_code = ?, preferred_channel = ?, display_name = ?, telegram_chat_id = COALESCE(?, telegram_chat_id), discord_user_id = COALESCE(?, discord_user_id) where id = ?",
                    (code, "telegram" if platform == "telegram" else "discord", display_name, value if platform == "telegram" else None, value if platform == "discord" else None, row["id"]),
                )
                conn.commit()
                row = conn.execute("select * from members where id = ?", (row["id"],)).fetchone()
        else:
            with conn.cursor() as cur:
                if platform == "telegram":
                    cur.execute("select * from members where telegram_chat_id = %s", [value])
                else:
                    cur.execute("select * from members where discord_user_id = %s", [value])
                existing = cur.fetchone()
                if existing is None:
                    cur.execute(
                        "insert into members(key, display_name, role, link_code, preferred_channel, telegram_chat_id, discord_user_id) values (%s, %s, 'member', %s, %s, %s, %s) returning *",
                        [code, display_name, code, "telegram" if platform == "telegram" else "discord", value if platform == "telegram" else None, value if platform == "discord" else None],
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise RuntimeError("Failed to insert member.")
                else:
                    row = dict(zip([desc[0] for desc in cur.description], existing))
                    cur.execute(
                        "update members set link_code = %s, preferred_channel = %s, display_name = %s, telegram_chat_id = COALESCE(%s, telegram_chat_id), discord_user_id = COALESCE(%s, discord_user_id) where id = %s returning *",
                        [code, "telegram" if platform == "telegram" else "discord", display_name, value if platform == "telegram" else None, value if platform == "discord" else None, row["id"]],
                    )
                    row = cur.fetchone()
                conn.commit()
    finally:
        conn.close()
    if is_sqlite():
        return dict(row)
    return row


def get_member_by_link_code(code: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        if is_sqlite():
            row = conn.execute("select * from members where link_code = ?", (code,)).fetchone()
        else:
            with conn.cursor() as cur:
                cur.execute("select * from members where link_code = %s", [code])
                row = cur.fetchone()
                if row is not None:
                    row = dict(zip([desc[0] for desc in cur.description], row))
    finally:
        conn.close()
    if row is None:
        return None
    if is_sqlite():
        return dict(row)
    return row


def create_task_from_preview(member_id: int, assignee_key: str, title: str, details: str | None, due_at: str | None) -> dict[str, Any]:
    assignee = get_member_by_key(assignee_key) if assignee_key else None
    if assignee is None:
        raise ValueError(f"Unknown assignee key: {assignee_key}")
    conn = get_connection()
    try:
        if is_sqlite():
            cursor = conn.execute(
                "insert into tasks(title, details, assignee_id, created_by, due_at, status, created_at) values (?, ?, ?, ?, ?, 'open', ?)",
                (title, details, assignee["id"], member_id, due_at, datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
            task_row = conn.execute("select * from tasks where id = ?", (cursor.lastrowid,)).fetchone()
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into tasks(title, details, assignee_id, created_by, due_at, status) values (%s, %s, %s, %s, %s, 'open') returning *",
                    [title, details, assignee["id"], member_id, due_at],
                )
                task_row = cur.fetchone()
                if task_row is None:
                    raise RuntimeError("Task insertion failed.")
                conn.commit()
    finally:
        conn.close()
    if is_sqlite():
        task = dict(task_row)
    else:
        columns = [desc[0] for desc in conn.cursor().description] if False else None
        task = dict(task_row)
    return task


def get_member_by_key(key: str) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        if is_sqlite():
            row = conn.execute("select * from members where key = ?", (key,)).fetchone()
        else:
            with conn.cursor() as cur:
                cur.execute("select * from members where key = %s", [key])
                row = cur.fetchone()
                if row is not None:
                    row = dict(zip([desc[0] for desc in cur.description], row))
    finally:
        conn.close()
    if row is None:
        return None
    if is_sqlite():
        return dict(row)
    return row


def list_tasks(member_id: int | None, scope: str = "all") -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        if is_sqlite():
            if scope == "mine" and member_id is not None:
                rows = conn.execute(
                    "select * from tasks where (assignee_id = ? or created_by = ?) order by created_at desc",
                    (member_id, member_id),
                ).fetchall()
            else:
                rows = conn.execute("select * from tasks order by created_at desc").fetchall()
        else:
            with conn.cursor() as cur:
                if scope == "mine" and member_id is not None:
                    cur.execute(
                        "select * from tasks where assignee_id = %s or created_by = %s order by created_at desc",
                        [member_id, member_id],
                    )
                else:
                    cur.execute("select * from tasks order by created_at desc")
                rows = cur.fetchall()
                if rows:
                    rows = [dict(zip([desc[0] for desc in cur.description], row)) for row in rows]
    finally:
        conn.close()
    if is_sqlite():
        return [dict(row) for row in rows]
    return rows


def mark_task_done(task_id: int, actor_member_id: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        if is_sqlite():
            row = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
            if row is None:
                raise ValueError("Task not found.")
            task = dict(row)
            if task["assignee_id"] != actor_member_id and not is_owner(actor_member_id):
                raise PermissionError("Only the assignee or owner may mark a task done.")
            conn.execute(
                "update tasks set status = 'done', done_at = ? where id = ?",
                (datetime.now(TIMEZONE).isoformat(), task_id),
            )
            conn.commit()
            updated = conn.execute("select * from tasks where id = ?", (task_id,)).fetchone()
        else:
            with conn.cursor() as cur:
                cur.execute("select * from tasks where id = %s", [task_id])
                task = dict(zip([desc[0] for desc in cur.description], cur.fetchone()))
                if task is None:
                    raise ValueError("Task not found.")
                if task["assignee_id"] != actor_member_id and not is_owner(actor_member_id):
                    raise PermissionError("Only the assignee or owner may mark a task done.")
                cur.execute(
                    "update tasks set status = 'done', done_at = %s where id = %s",
                    [datetime.now(TIMEZONE).isoformat(), task_id],
                )
                conn.commit()
                cur.execute("select * from tasks where id = %s", [task_id])
                updated = cur.fetchone()
                updated = dict(zip([desc[0] for desc in cur.description], updated))
    finally:
        conn.close()
    if is_sqlite():
        return dict(updated)
    return updated


def is_owner(member_id: int) -> bool:
    conn = get_connection()
    try:
        if is_sqlite():
            row = conn.execute("select role from members where id = ?", (member_id,)).fetchone()
        else:
            with conn.cursor() as cur:
                cur.execute("select role from members where id = %s", [member_id])
                row = cur.fetchone()
    finally:
        conn.close()
    return bool(row and row[0] == "owner")


def create_document(kind: str, title: str, body_md: str, created_by: int) -> dict[str, Any]:
    conn = get_connection()
    try:
        if is_sqlite():
            cursor = conn.execute(
                "insert into documents(kind, title, body_md, status, created_by, created_at) values (?, ?, ?, 'draft', ?, ?)",
                (kind, title, body_md, created_by, datetime.now(TIMEZONE).isoformat()),
            )
            conn.commit()
            row = conn.execute("select * from documents where id = ?", (cursor.lastrowid,)).fetchone()
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into documents(kind, title, body_md, status, created_by) values (%s, %s, %s, 'draft', %s) returning *",
                    [kind, title, body_md, created_by],
                )
                row = cur.fetchone()
                conn.commit()
    finally:
        conn.close()
    return dict(row)


def approve_document(doc_id: int, member_id: int) -> dict[str, Any]:
    if not is_owner(member_id):
        raise PermissionError("Only the owner may approve documents.")
    conn = get_connection()
    try:
        if is_sqlite():
            existing = conn.execute("select * from documents where id = ?", (doc_id,)).fetchone()
            if existing is None:
                raise ValueError("Document not found.")
            doc = dict(existing)
            number = f"CMD-{doc_id:04d}"
            conn.execute(
                "update documents set status = 'approved', number = ?, approved_by = ? where id = ?",
                (number, member_id, doc_id),
            )
            conn.commit()
            updated = conn.execute("select * from documents where id = ?", (doc_id,)).fetchone()
        else:
            with conn.cursor() as cur:
                cur.execute("select * from documents where id = %s", [doc_id])
                doc = dict(zip([desc[0] for desc in cur.description], cur.fetchone()))
                if doc is None:
                    raise ValueError("Document not found.")
                number = f"CMD-{doc_id:04d}"
                cur.execute(
                    "update documents set status = 'approved', number = %s, approved_by = %s where id = %s returning *",
                    [number, member_id, doc_id],
                )
                updated = cur.fetchone()
                conn.commit()
    finally:
        conn.close()
    return dict(updated)


def get_document(doc_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        if is_sqlite():
            row = conn.execute("select * from documents where id = ?", (doc_id,)).fetchone()
        else:
            with conn.cursor() as cur:
                cur.execute("select * from documents where id = %s", [doc_id])
                row = cur.fetchone()
                if row is not None:
                    row = dict(zip([desc[0] for desc in cur.description], row))
    finally:
        conn.close()
    if row is None:
        return None
    if is_sqlite():
        return dict(row)
    return row


def log_action(member_id: int | None, action: str, payload: dict[str, Any]) -> None:
    conn = get_connection()
    try:
        if is_sqlite():
            conn.execute(
                "insert into audit_log(member_id, action, payload) values (?, ?, ?)",
                (member_id, action, json.dumps(payload, default=str)),
            )
            conn.commit()
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into audit_log(member_id, action, payload) values (%s, %s, %s)",
                    [member_id, action, json.dumps(payload, default=str)],
                )
                conn.commit()
    finally:
        conn.close()


def log_llm_call(purpose: str, model: str, in_tokens: int, out_tokens: int, ok: bool) -> None:
    conn = get_connection()
    try:
        if is_sqlite():
            conn.execute(
                "insert into llm_calls(purpose, model, in_tokens, out_tokens, ok) values (?, ?, ?, ?, ?)",
                (purpose, model, in_tokens, out_tokens, ok),
            )
            conn.commit()
        else:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into llm_calls(purpose, model, in_tokens, out_tokens, ok) values (%s, %s, %s, %s, %s)",
                    [purpose, model, in_tokens, out_tokens, ok],
                )
                conn.commit()
    finally:
        conn.close()


def call_gemini_parse(prompt: str, roster_map: dict[str, str]) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL")
    if not api_key or not model:
        return parse_task_intent(prompt, roster_map)
    # This branch intentionally uses the official google-genai client when configured.
    try:
        import google.genai as genai
    except Exception:
        return parse_task_intent(prompt, roster_map)
    client = genai.Client(api_key=api_key)
    schema = {
        "type": "OBJECT",
        "properties": {
            "kind": {"type": "STRING"},
            "title": {"type": "STRING"},
            "assignee_key": {"type": "STRING"},
            "due_at": {"type": ["STRING", "NULL"]},
            "detail": {"type": "STRING"},
        },
        "required": ["kind", "title", "assignee_key", "due_at", "detail"],
    }
    response = client.models.generate_content(
        model=model,
        contents=f"Return JSON matching this schema. roster: {roster_map}. Prompt: {prompt}",
        config={"response_mime_type": "application/json", "response_schema": schema},
    )
    payload = json.loads(response.text)
    log_llm_call("parse", model, 0, 0, True)
    return payload


def render_document_md(document: dict[str, Any]) -> str:
    banner = "DRAFT, not a tax document"
    return f"# {document['title']}\n\n> {banner}\n\n{document['body_md']}"


def create_preview_for_task(member_id: int, text: str) -> dict[str, Any]:
    roster_map = roster()
    payload = call_gemini_parse(text, roster_map)
    assignee_key = payload.get("assignee_key")
    if assignee_key is None:
        raise ValueError("I could not identify the assignee from the task text.")
    task = {
        "title": payload.get("title") or text,
        "assignee_key": assignee_key,
        "due_at": payload.get("due_at"),
        "details": payload.get("detail") or text,
    }
    preview_text = (
        f"Preview: {task['title']} | assignee={task['assignee_key']} | due={task['due_at'] or 'no due date'}"
    )
    return {"status": "preview", "text": preview_text, "preview": task, "member_id": member_id}


def create_task_from_preview_data(member_id: int, task: dict[str, Any]) -> dict[str, Any]:
    created = create_task_from_preview(
        member_id=member_id,
        assignee_key=task["assignee_key"],
        title=task["title"],
        details=task.get("details") or task["title"],
        due_at=task.get("due_at"),
    )
    log_action(member_id, "task_created", {"task_id": created["id"], "title": created["title"]})
    return created


def verify_discord_signature(raw_body: bytes, signature_hex: str | None, timestamp: str | None) -> bool:
    if not signature_hex or not timestamp:
        return False
    public_key_hex = os.getenv("DISCORD_PUBLIC_KEY", "")
    if not public_key_hex:
        return False
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key_hex))
        signed = f"{timestamp}.{raw_body.decode('utf-8')}".encode()
        verify_key.verify(signed, bytes.fromhex(signature_hex))
        return True
    except (BadSignatureError, ValueError):
        return False


def handle_telegram_message(payload: dict[str, Any]) -> dict[str, Any]:
    text = None
    message = payload.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if message.get("text"):
        text = message["text"]
    if not text:
        return {"status": "ignored", "reason": "empty message"}

    if is_duplicate_event("telegram", str(payload.get("update_id"))):
        return {"status": "duplicate", "ignored": True}
    record_event("telegram", str(payload.get("update_id")))

    if text.startswith("/link "):
        code = text.split("/link ", 1)[1].strip()
        member = ensure_member_for_link("telegram", str(chat_id), code, message.get("from", {}).get("first_name", "Telegram user"))
        return {"status": "linked", "member": member}

    member = member_from_chat("telegram", chat_id)
    if member is None:
        return {"status": "error", "message": "not linked"}

    if text.startswith("/tasks"):
        scope = "mine" if "mine" in text.lower() else "all"
        rows = list_tasks(member["id"], scope)
        return {"status": "tasks", "items": rows}

    if text.startswith("/done "):
        task_id_raw = text.replace("/done", "", 1).strip()
        try:
            task_id = int(task_id_raw)
            task = mark_task_done(task_id, member["id"])
            return {"status": "done", "task": task}
        except (ValueError, PermissionError) as exc:
            return {"status": "error", "message": str(exc)}

    if text.startswith("/draft "):
        rest = text[len("/draft ") :].strip()
        parts = rest.split(" ", 1)
        if len(parts) != 2:
            return {"status": "error", "message": "Usage: /draft <message|quote|proforma> <text>"}
        kind = parts[0].strip().lower(); body = parts[1].strip()
        doc = create_document(kind, body.splitlines()[0][:80] or "Draft", body, member["id"])
        render = render_document_md(doc)
        return {"status": "draft", "document": doc, "markdown": render}

    if text.startswith("/approve "):
        try:
            document_id = int(text.replace("/approve", "", 1).strip())
            doc = approve_document(document_id, member["id"])
            return {"status": "approved", "document": doc}
        except (ValueError, PermissionError) as exc:
            return {"status": "error", "message": str(exc)}

    try:
        preview = create_preview_for_task(member["id"], text)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    return {"status": "preview", "preview": preview}


def handle_discord_payload(payload: dict[str, Any]) -> dict[str, Any]:
    interaction_type = payload.get("type")
    if interaction_type == 1:
        return {"type": 1}
    if interaction_type == 2:
        return {"type": 5}
    if interaction_type == 3:
        data = payload.get("data", {})
        custom_id = data.get("custom_id")
        if custom_id and custom_id.startswith("confirm_task_"):
            task_data = json.loads(custom_id.split("_", 2)[-1])
            return {"status": "confirmed", "task": create_task_from_preview_data(payload.get("member_id", 1), task_data)}
        return {"type": 5}
    return {"status": "ignored"}


def get_due_tasks() -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        if is_sqlite():
            rows = conn.execute(
                "select * from tasks where status = 'open' order by created_at desc"
            ).fetchall()
            rows = [dict(row) for row in rows]
        else:
            with conn.cursor() as cur:
                cur.execute("select * from tasks where status = 'open' order by created_at desc")
                rows = cur.fetchall()
                rows = [dict(zip([desc[0] for desc in cur.description], row)) for row in rows]
    finally:
        conn.close()
    return rows


def tick_reminders() -> dict[str, Any]:
    now = now_local()
    today = now.date()
    sent: list[str] = []
    for task in get_due_tasks():
        due_at = parse_iso_datetime(task.get("due_at")) if task.get("due_at") else None
        assignee = get_member_by_key(task["assignee_id"]) if False else None
        assignee = get_member_by_id(task["assignee_id"])
        if due_at is None:
            continue
        reminder_due = due_at - now <= timedelta(days=1)
        overdue = due_at < now and task.get("overdue_notified_on") != today.isoformat()
        needs_reminder = reminder_due and task.get("reminded_at") is None
        if needs_reminder or overdue:
            if assignee:
                channel = assignee.get("preferred_channel") or "telegram"
                message = (
                    f"Task #{task['id']} is due soon: {task['title']}"
                    if needs_reminder
                    else f"Task #{task['id']} is overdue: {task['title']}"
                )
                sent.append(message)
                if is_sqlite():
                    conn = get_connection()
                    conn.execute(
                        "update tasks set reminded_at = ?, overdue_notified_on = ? where id = ?",
                        (now.isoformat(), today.isoformat() if overdue else task.get("overdue_notified_on"), task["id"]),
                    )
                    conn.commit()
                    conn.close()
                else:
                    with get_connection() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                "update tasks set reminded_at = %s, overdue_notified_on = %s where id = %s",
                                [now.isoformat(), today.isoformat() if overdue else task.get("overdue_notified_on"), task["id"]],
                            )
                            conn.commit()
            else:
                continue
    return {"status": "ok", "sent": sent}


def get_member_by_id(member_id: int) -> dict[str, Any] | None:
    conn = get_connection()
    try:
        if is_sqlite():
            row = conn.execute("select * from members where id = ?", (member_id,)).fetchone()
        else:
            with conn.cursor() as cur:
                cur.execute("select * from members where id = %s", [member_id])
                row = cur.fetchone()
                if row is not None:
                    row = dict(zip([desc[0] for desc in cur.description], row))
    finally:
        conn.close()
    if row is None:
        return None
    if is_sqlite():
        return dict(row)
    return row


@asynccontextmanager
async def lifespan(_: FastAPI):
    with _lock:
        apply_migrations()
        ensure_seed_members()
    yield


app = FastAPI(title="Command Hub", lifespan=lifespan)
_lock = threading.Lock()


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/internal/tick")
def internal_tick(x_internal_secret: str = Header(default="")) -> dict[str, Any]:
    expected_secret = os.getenv("INTERNAL_SECRET", "dev-internal-secret")
    if x_internal_secret != expected_secret:
        raise HTTPException(status_code=401, detail="invalid secret")
    return tick_reminders()


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> JSONResponse:
    payload = await request.json()
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret and secret != os.getenv("TELEGRAM_SECRET", "dev-telegram-secret"):
        return JSONResponse(status_code=401, content={"error": "invalid telegram secret"})
    result = handle_telegram_message(payload)
    return JSONResponse(content=result)


@app.post("/discord/interactions")
async def discord_interactions(request: Request) -> JSONResponse:
    raw = await request.body()
    signature = request.headers.get("X-Signature-Ed25519")
    timestamp = request.headers.get("X-Signature-Timestamp")
    if not verify_discord_signature(raw, signature, timestamp):
        return JSONResponse(status_code=401, content={"error": "invalid signature"})
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})
    result = handle_discord_payload(payload)
    return JSONResponse(content=result)


@app.post("/internal/confirm")
def confirm_task(body: dict[str, Any]) -> dict[str, Any]:
    task = body.get("task") or {}
    member_id = body.get("member_id")
    if not member_id:
        raise HTTPException(status_code=400, detail="member_id is required")
    created = create_task_from_preview_data(int(member_id), task)
    return {"status": "created", "task": created}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.command_hub:app", host="0.0.0.0", port=8000, reload=True)
