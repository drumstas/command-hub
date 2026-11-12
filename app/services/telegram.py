from __future__ import annotations

import asyncio
import hmac
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

import httpx
from fastapi import Request
from fastapi.responses import JSONResponse

from ..database import (
    execute,
    fetchall,
    get_member_by_id,
    is_duplicate_event,
    is_sqlite,
    link_member,
    log_action,
    member_from_chat,
    record_event,
)
from ..services.discord import send_task_assignment
from ..tasks import (
    approve_pending_task,
    classify_message,
    create_pending_task,
    format_task,
    format_task_intent,
    list_tasks,
    mark_task_done,
    now_local,
    parse_iso_datetime,
    pending_task,
    summarize_tasks,
)


async def api(method: str, data: dict[str, Any]) -> dict[str, Any]:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN nesukonfigūruotas.")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{token}/{method}", json=data
        )
        response.raise_for_status()
        payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Telegram API {method} užklausa nepavyko.")
    return payload


async def send_message(
    chat_id: int | str,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> None:
    data: dict[str, Any] = {"chat_id": chat_id, "text": text}
    if reply_markup:
        data["reply_markup"] = reply_markup
    await api("sendMessage", data)


async def voice_bytes(file_id: str) -> tuple[bytes, str]:
    file_info = await api("getFile", {"file_id": file_id})
    path = file_info.get("result", {}).get("file_path")
    if not path:
        raise RuntimeError("Telegram nepateikė balso failo kelio.")
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    async with httpx.AsyncClient(timeout=40) as client:
        response = await client.get(f"https://api.telegram.org/file/bot{token}/{path}")
        response.raise_for_status()
    return response.content, "audio/ogg"


async def fetch_message_text(message: dict[str, Any]) -> tuple[str, bytes | None, str]:
    text = message.get("text") or message.get("caption") or ""
    voice = message.get("voice") or message.get("audio")
    if not voice:
        return text, None, "audio/ogg"
    audio, mime_type = await voice_bytes(voice["file_id"])
    mime_type = voice.get("mime_type") or mime_type
    return text, audio, mime_type


async def handle_message(update: dict[str, Any]) -> dict[str, Any]:
    event_id = str(update.get("update_id", ""))
    if not event_id:
        return {"status": "ignored"}
    if is_duplicate_event("telegram", event_id):
        return {"status": "duplicate"}
    message = update.get("message") or {}
    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None:
        return {"status": "ignored"}
    sender = message.get("from") or {}
    text, audio, _ = await fetch_message_text(message)

    if text.startswith("/link "):
        member = link_member(
            "telegram",
            int(chat_id),
            text.split(maxsplit=1)[1].strip(),
            sender.get("first_name", "Telegram naudotojas"),
        )
        if member is None:
            await send_message(
                chat_id, "Neteisingas arba nebegaliojantis susiejimo kodas."
            )
            record_event("telegram", event_id)
            return {"status": "error", "message": "invalid link code"}
        await send_message(
            chat_id,
            "Paskyra susieta. Dabar galite teikti užduotis ir klausti apie jas.",
        )
        record_event("telegram", event_id)
        return {"status": "linked"}

    member = member_from_chat("telegram", chat_id)
    if member is None:
        await send_message(
            chat_id, "Paskyra nesusieta. Susiekite ją komanda /link KODAS."
        )
        record_event("telegram", event_id)
        return {"status": "error", "message": "not linked"}

    if text.startswith("/tasks"):
        scope = "mine" if "mine" in text.casefold() else "all"
        tasks = list_tasks(member["id"], scope)
        answer = "\n".join(format_task(task) for task in tasks) or "Užduočių nėra."
        await send_message(chat_id, answer[:4000])
        record_event("telegram", event_id)
        return {"status": "tasks", "count": len(tasks)}

    if text.startswith("/done "):
        try:
            task_id = int(text.split(maxsplit=1)[1])
            task = mark_task_done(task_id, member["id"])
        except (ValueError, PermissionError) as error:
            await send_message(chat_id, str(error))
            record_event("telegram", event_id)
            return {"status": "error", "message": str(error)}
        await send_message(chat_id, f"Užduotis #{task_id} pažymėta atlikta.")
        record_event("telegram", event_id)
        return {"status": "done", "task_id": task["id"]}

    if not text.strip() and audio is None:
        await send_message(chat_id, "Parašykite užduotį arba atsiųskite balso žinutę.")
        record_event("telegram", event_id)
        return {"status": "empty"}

    intent = await asyncio.to_thread(classify_message, text, audio)
    if intent.action == "query_tasks":
        due_before = parse_iso_datetime(intent.due_before)
        tasks = list_tasks(
            assignee_key=intent.assignee_key,
            status=intent.status or "open",
            due_before=due_before.isoformat() if due_before else None,
        )
        question = intent.query_text or text or "Užklausa apie esamas užduotis."
        answer = await asyncio.to_thread(summarize_tasks, question, tasks)
        await send_message(chat_id, answer[:4000])
        record_event("telegram", event_id)
        return {"status": "query", "count": len(tasks)}

    if intent.action != "create_task":
        raise ValueError("Neatpažintas prašymo tipas.")
    owner_chat_id = os.getenv("OWNER_TELEGRAM_CHAT_ID")
    if not owner_chat_id:
        raise RuntimeError("Nenustatytas administratoriaus Telegram pokalbio ID.")
    token = create_pending_task(event_id, member["id"], intent)
    await send_message(
        owner_chat_id,
        format_task_intent(intent),
        {
            "inline_keyboard": [[
                {"text": "Patvirtinti", "callback_data": f"task:approve:{token}"},
                {"text": "Atmesti", "callback_data": f"task:reject:{token}"},
            ]]
        },
    )
    await send_message(
        chat_id, "Užduoties pasiūlymas nusiųstas administratoriui patvirtinti."
    )
    record_event("telegram", event_id)
    return {"status": "pending_approval"}


async def handle_callback(update: dict[str, Any]) -> dict[str, Any]:
    callback = update.get("callback_query") or {}
    callback_id = callback.get("id")
    event_id = str(update.get("update_id", ""))
    data = callback.get("data", "")
    sender_id = (callback.get("from") or {}).get("id")
    chat_id = ((callback.get("message") or {}).get("chat") or {}).get("id")
    if not callback_id or not event_id or not chat_id:
        return {"status": "ignored"}
    if is_duplicate_event("telegram", event_id):
        await api("answerCallbackQuery", {"callback_query_id": callback_id})
        return {"status": "duplicate"}
    expected_user_id = os.getenv("OWNER_TELEGRAM_USER_ID") or os.getenv(
        "OWNER_TELEGRAM_CHAT_ID"
    )
    if not expected_user_id or str(sender_id) != expected_user_id:
        await api(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id,
                "text": "Tik administratorius gali peržiūrėti šį prašymą.",
                "show_alert": True,
            },
        )
        record_event("telegram", event_id)
        return {"status": "unauthorized"}
    if not data.startswith(("task:approve:", "task:reject:")):
        await api("answerCallbackQuery", {"callback_query_id": callback_id})
        record_event("telegram", event_id)
        return {"status": "ignored"}
    _, action, token = data.split(":", 2)
    pending = pending_task(token)
    if pending is None:
        await api(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": "Prašymas nebegalioja."},
        )
        record_event("telegram", event_id)
        return {"status": "not_found"}
    if action == "approve":
        if not pending.get("assignee_discord_user_id"):
            await api(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": "Pirmiausia susiekite užduoties gavėją su Discord paskyra.",
                    "show_alert": True,
                },
            )
            return {"status": "assignee_not_linked"}
        if not os.getenv("DISCORD_BOT_TOKEN") or not os.getenv(
            "DISCORD_TASK_CHANNEL_ID"
        ):
            await api(
                "answerCallbackQuery",
                {
                    "callback_query_id": callback_id,
                    "text": "Discord pranešimų kanalas dar nesukonfigūruotas.",
                    "show_alert": True,
                },
            )
            return {"status": "discord_not_configured"}
        task = approve_pending_task(token, int(sender_id))
        await send_task_assignment(task)
        execute(
            "update tasks set discord_notified_at = ? where id = ?",
            (
                now_local().isoformat() if is_sqlite() else now_local(),
                task["id"],
            ),
        )
        await api(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": "Užduotis patvirtinta."},
        )
        await send_message(chat_id, f"Patvirtinta: {format_task(task)}")
        requester = get_member_by_id(pending["requester_id"])
        if requester and requester.get("telegram_chat_id"):
            await send_message(
                requester["telegram_chat_id"],
                f"Administratorius patvirtino užduotį #{task['id']}: {task['title']}",
            )
        log_action(pending["requester_id"], "task_approved", {"task_id": task["id"]})
    else:
        expected = os.getenv("OWNER_TELEGRAM_USER_ID") or os.getenv(
            "OWNER_TELEGRAM_CHAT_ID"
        )
        if not expected or str(sender_id) != expected:
            raise PermissionError("Tik administratorius gali atmesti užduotis.")
        execute("delete from pending_tasks where token = ?", (token,))
        await api(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": "Užduotis atmesta."},
        )
        await send_message(chat_id, "Užduoties pasiūlymas atmestas.")
        requester = get_member_by_id(pending["requester_id"])
        if requester and requester.get("telegram_chat_id"):
            await send_message(
                requester["telegram_chat_id"],
                "Administratorius atmetė užduoties pasiūlymą.",
            )
    record_event("telegram", event_id)
    return {"status": action}


async def _report_error(
    update: dict[str, Any], error: Exception, message_text: str | None = None
) -> None:
    message = (
        update.get("message")
        or update.get("callback_query", {}).get("message")
        or {}
    )
    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None:
        logging.getLogger(__name__).error("Could not report Telegram error: %s", error)
        return
    try:
        await send_message(
            chat_id,
            message_text or f"Nepavyko apdoroti prašymo: {error}",
        )
    except Exception:
        logging.getLogger(__name__).exception("Could not send Telegram error response")


async def webhook(request: Request) -> JSONResponse:
    expected = os.getenv("TELEGRAM_SECRET")
    actual = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not expected or not hmac.compare_digest(actual, expected):
        return JSONResponse(status_code=401, content={"error": "invalid telegram secret"})
    update: dict[str, Any] = {}
    try:
        update = await request.json()
        result = (
            await handle_callback(update)
            if "callback_query" in update
            else await handle_message(update)
        )
    except (ValueError, PermissionError) as error:
        await _report_error(update, error)
        event_id = str(update.get("update_id", ""))
        if event_id:
            record_event("telegram", event_id)
        return JSONResponse(status_code=200, content={"ok": True, "message": str(error)})
    except Exception as error:
        logging.getLogger(__name__).exception("Telegram update processing failed")
        await _report_error(
            update,
            error,
            "Laikinai nepavyko apdoroti užklausos. Pabandykite dar kartą.",
        )
        return JSONResponse(status_code=200, content={"ok": True})
    return JSONResponse(content={"ok": True, "status": result.get("status", "ok")})


async def send_due_reminders(now: datetime) -> int:
    tasks = fetchall(
        """select t.*, m.telegram_chat_id from tasks t
           join members m on m.id = t.assignee_id
           where t.status = 'open' and t.due_at is not null
           order by t.due_at asc"""
    )
    sent = 0
    for task in tasks:
        due = parse_iso_datetime(task["due_at"])
        if due is None or not task.get("telegram_chat_id"):
            continue
        previous_overdue_date = task["overdue_notified_on"]
        overdue_notified = previous_overdue_date
        if isinstance(overdue_notified, date):
            overdue_notified = overdue_notified.isoformat()
        reminder_due = due <= now + timedelta(hours=24) and not task["reminded_at"]
        overdue_due = due < now and overdue_notified != now.date().isoformat()
        if not reminder_due and not overdue_due:
            continue
        message = (
            f"Vėluoja užduotis #{task['id']} „{task['title']}“"
            if overdue_due
            else f"Primename: užduotis #{task['id']} „{task['title']}“"
        )
        await send_message(task["telegram_chat_id"], message)
        execute(
            """update tasks set reminded_at = ?, overdue_notified_on = ?
               where id = ? and status = 'open'""",
            (
                now.isoformat()
                if reminder_due and is_sqlite()
                else (now if reminder_due else task["reminded_at"]),
                now.date().isoformat()
                if overdue_due and is_sqlite()
                else (now.date() if overdue_due else previous_overdue_date),
                task["id"],
            ),
        )
        sent += 1
    return sent
