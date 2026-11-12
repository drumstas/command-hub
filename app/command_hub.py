from __future__ import annotations

import hmac
import json
import os
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from .database import (
    APP_ROOT,
    apply_migrations,
    ensure_seed_members,
    execute,
    fetchall,
    is_duplicate_event,
    is_sqlite,
    link_member,
    member_from_chat,
    record_event,
)
from .services import telegram
from .services.discord import send_task_assignment
from .tasks import (
    format_task,
    list_tasks,
    mark_task_done,
    now_local,
)

load_dotenv(APP_ROOT / ".env")


def verify_discord_signature(raw_body: bytes, signature_hex: str | None, timestamp: str | None) -> bool:
    if not signature_hex or not timestamp:
        return False
    public_key_hex = os.getenv("DISCORD_PUBLIC_KEY", "")
    try:
        verify_key = VerifyKey(bytes.fromhex(public_key_hex))
        verify_key.verify(
            f"{timestamp}.{raw_body.decode('utf-8')}".encode(),
            bytes.fromhex(signature_hex),
        )
        return True
    except (BadSignatureError, ValueError):
        return False


@asynccontextmanager
async def lifespan(_: FastAPI):
    apply_migrations()
    ensure_seed_members()
    yield


app = FastAPI(title="Command Hub — task management only", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/telegram/webhook")
async def telegram_webhook(request: Request) -> JSONResponse:
    return await telegram.webhook(request)


@app.post("/internal/tick")
async def internal_tick(x_internal_secret: str = Header(default="")) -> dict[str, Any]:
    expected = os.getenv("INTERNAL_SECRET", "")
    if not expected or not hmac.compare_digest(x_internal_secret, expected):
        raise HTTPException(status_code=401, detail="invalid secret")
    now = now_local()
    discord_tasks = fetchall(
        """select t.*, m.discord_user_id as assignee_discord_user_id,
                  m.display_name as assignee_name, r.display_name as requester_name
           from tasks t
           join members m on m.id = t.assignee_id
           join members r on r.id = t.created_by
           where t.discord_notified_at is null and m.discord_user_id is not null
           order by t.created_at asc"""
    )
    discord_sent = 0
    for task in discord_tasks:
        await send_task_assignment(task)
        execute(
            "update tasks set discord_notified_at = ? where id = ?",
            (now.isoformat() if is_sqlite() else now, task["id"]),
        )
        discord_sent += 1
    sent = await telegram.send_due_reminders(now)
    return {"status": "ok", "sent": sent, "discord_sent": discord_sent}


@app.post("/discord/interactions")
async def discord_interactions(request: Request) -> JSONResponse:
    raw = await request.body()
    if not verify_discord_signature(
        raw,
        request.headers.get("X-Signature-Ed25519"),
        request.headers.get("X-Signature-Timestamp"),
    ):
        return JSONResponse(status_code=401, content={"error": "invalid signature"})
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return JSONResponse(status_code=400, content={"error": "invalid JSON"})
    if payload.get("type") == 1:
        return JSONResponse(content={"type": 1})
    if payload.get("type") != 2:
        return JSONResponse(content={"type": 4, "data": {"content": "Ši sąveika nepalaikoma.", "flags": 64}})
    data = payload.get("data") or {}
    command = data.get("name")
    options = {
        option.get("name"): option.get("value")
        for option in data.get("options", [])
    }
    user = payload.get("user") or (payload.get("member") or {}).get("user") or {}
    discord_id = user.get("id")
    event_id = str(payload.get("id", ""))
    if event_id and is_duplicate_event("discord", event_id):
        return JSONResponse(content={"type": 4, "data": {"content": "Ši komanda jau apdorota.", "flags": 64}})
    member = member_from_chat("discord", discord_id or "")
    if command == "link":
        linked = link_member(
            "discord",
            str(discord_id or ""),
            str(options.get("code", "")),
            user.get("username", "Discord naudotojas"),
        )
        content = "Paskyra susieta." if linked else "Neteisingas arba nebegaliojantis susiejimo kodas."
    elif member is None:
        content = "Paskyra nesusieta. Susiekite ją komanda /link."
    elif command == "tasks":
        scope = "mine" if options.get("scope") == "mine" else "all"
        tasks = list_tasks(member["id"], scope)
        content = "\n".join(format_task(task) for task in tasks) or "Užduočių nėra."
    elif command == "done":
        try:
            task = mark_task_done(int(options.get("task_id")), member["id"])
            content = f"Užduotis #{task['id']} pažymėta atlikta."
        except (ValueError, PermissionError, TypeError) as error:
            content = str(error)
    else:
        content = "Nežinoma komanda."
    if event_id:
        record_event("discord", event_id)
    return JSONResponse(
        content={"type": 4, "data": {"content": content[:1900], "flags": 64}}
    )
