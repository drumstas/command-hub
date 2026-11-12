from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from app import command_hub as hub
from app import database, tasks
from app.services import discord as discord_service
from app.services import telegram


@pytest.fixture
def client(tmp_path, monkeypatch):
    database = tmp_path / "command_hub.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("TELEGRAM_SECRET", "telegram-secret")
    monkeypatch.setenv("INTERNAL_SECRET", "internal-secret")
    monkeypatch.setenv("OWNER_TELEGRAM_CHAT_ID", "900")
    monkeypatch.setenv("OWNER_TELEGRAM_USER_ID", "900")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-bot-token")
    monkeypatch.setenv("DISCORD_TASK_CHANNEL_ID", "700")
    monkeypatch.setenv("TEAM_ROSTER", json.dumps([
        {"key": "owner", "display_name": "Administrator"},
        {
            "key": "alex",
            "display_name": "Alex",
            "telegram_chat_id": 200,
            "discord_user_id": "alex-discord",
        },
        {"key": "jonas", "display_name": "Jonas", "telegram_chat_id": 1001},
        {"key": "marta", "display_name": "Marta"},
    ]))
    with TestClient(hub.app) as test_client:
        yield test_client


def test_health_and_task_only_idempotent_migrations(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    hub.apply_migrations()
    hub.apply_migrations()
    tables = {
        row["name"] for row in hub.fetchall(
            "select name from sqlite_master where type = 'table'"
        )
    }
    assert "tasks" in tables
    assert "pending_tasks" in tables
    columns = {
        row["name"] for row in hub.fetchall("pragma table_info(tasks)")
    }
    assert "discord_notified_at" in columns
    assert "documents" not in tables
    assert "llm_calls" not in tables


def test_admin_is_seeded_as_a_task_roster_member(client):
    owner = database.get_member_by_key("owner")

    assert owner["display_name"] == "Administrator"
    assert owner["role"] == "owner"
    assert owner["telegram_chat_id"] == 900
    assert owner["key"] in database.roster()
    linked_owner = database.assign_member_account("discord", "9000", "owner")
    assert linked_owner["role"] == "owner"
    assert linked_owner["discord_user_id"] == "9000"


def test_existing_non_task_tables_are_not_dropped(tmp_path, monkeypatch):
    database = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("create table schema_migrations(version integer primary key)")
    connection.execute("insert into schema_migrations(version) values (1), (2)")
    connection.execute("create table tasks(id integer primary key)")
    connection.execute("create table documents(id integer primary key, body text)")
    connection.execute("create table llm_calls(id integer primary key)")
    connection.commit()
    connection.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")

    hub.apply_migrations()

    connection = sqlite3.connect(database)
    remaining = {
        row[0]
        for row in connection.execute(
            "select name from sqlite_master where type = 'table'"
        )
    }
    assert "documents" in remaining
    assert "llm_calls" in remaining
    assert "discord_notified_at" in {
        row[1] for row in connection.execute("pragma table_info(tasks)")
    }
    connection.close()


def test_admin_assigns_member_ids_from_telegram_and_members_cannot(client, monkeypatch):
    sent = []

    async def capture_send(chat_id, text, reply_markup=None):
        sent.append((str(chat_id), text))

    monkeypatch.setattr(telegram, "send_message", capture_send)
    update = {
        "update_id": 8,
        "message": {
            "chat": {"id": 900},
            "from": {"id": 900},
            "text": "/link alex discord 3000",
        },
    }

    result = asyncio.run(telegram.handle_message(update))

    assert result["status"] == "linked"
    assert database.get_member_by_key("alex")["discord_user_id"] == "3000"

    update["update_id"] = 10
    update["message"]["text"] = "/link marta telegram 301"
    result = asyncio.run(telegram.handle_message(update))
    assert result["status"] == "linked"
    assert database.get_member_by_key("marta")["telegram_chat_id"] == 301

    unauthorized = {
        "update_id": 11,
        "message": {
            "chat": {"id": 1001},
            "from": {"id": 1001},
            "text": "/link marta telegram 301",
        },
    }
    result = asyncio.run(telegram.handle_message(unauthorized))
    assert result["status"] == "unauthorized"
    assert database.get_member_by_key("marta")["telegram_chat_id"] == 301
    assert "Tik administratorius" in sent[-1][1]


def test_admin_account_assignment_validates_roster_and_unique_ids(client):
    with pytest.raises(ValueError, match="was not found"):
        database.assign_member_account("discord", "123", "unknown")
    with pytest.raises(ValueError, match="numeric ID"):
        database.assign_member_account("discord", "alex-id", "alex")
    with pytest.raises(ValueError, match="already assigned"):
        database.assign_member_account("discord", "3000", "alex")
        database.assign_member_account("discord", "3000", "marta")


def test_member_lookup_binds_postgres_id_types_by_platform(monkeypatch):
    calls = []

    def capture_fetchone(sql, params):
        calls.append((sql, params))

    monkeypatch.setattr(database, "fetchone", capture_fetchone)
    database.member_from_chat("telegram", "123")
    database.member_from_chat("discord", 456)

    assert calls[0][1] == (123,)
    assert calls[1][1] == ("456",)


def test_task_is_only_persisted_after_admin_approval(client, monkeypatch):
    sent = []

    async def capture_send(chat_id, text, reply_markup=None):
        sent.append((str(chat_id), text, reply_markup))

    monkeypatch.setattr(telegram, "send_message", capture_send)
    monkeypatch.setattr(
        telegram,
        "classify_message",
        lambda text, audio=None, audio_mime_type="audio/ogg": tasks.TaskIntent(
            action="create_task",
            title="Parengti pasiūlymą",
            details="Klientui Acme",
            assignee_key="alex",
            due_at="2030-12-31T15:00:00+02:00",
        ),
    )
    update = {
        "update_id": 10,
        "message": {
            "chat": {"id": 1001},
            "from": {"id": 1001, "first_name": "Jonas"},
            "text": "Sukurk užduotį Alexui: parengti pasiūlymą iki 2030-12-31",
        },
    }
    result = asyncio.run(telegram.handle_message(update))
    assert result["status"] == "pending_approval"
    assert hub.fetchall("select * from tasks") == []
    pending = hub.fetchall("select * from pending_tasks")
    assert len(pending) == 1
    assert sent[0][0] == "900"
    keyboard = sent[0][2]["inline_keyboard"][0]
    assert keyboard[0]["callback_data"] == f"task:approve:{pending[0]['token']}"

    callback = {
        "update_id": 11,
        "callback_query": {
            "id": "callback-1",
            "from": {"id": 900},
            "message": {"chat": {"id": 900}},
            "data": f"task:approve:{pending[0]['token']}",
        },
    }
    callback_api_calls = []
    discord_notifications = []

    async def capture_api(method, data):
        callback_api_calls.append((method, data))
        return {"ok": True}

    async def capture_discord(task):
        discord_notifications.append(task)

    monkeypatch.setattr(telegram, "api", capture_api)
    monkeypatch.setattr(telegram, "send_task_assignment", capture_discord)
    approved = asyncio.run(telegram.handle_callback(callback))
    assert approved["status"] == "approve"
    created_tasks = hub.fetchall("select * from tasks")
    assert len(created_tasks) == 1
    assert created_tasks[0]["status"] == "open"
    assert hub.fetchall("select * from pending_tasks") == []
    assert sent[-1][0] == "1001"
    assert discord_notifications[0]["assignee_discord_user_id"] == "alex-discord"
    assert discord_notifications[0]["title"] == "Parengti pasiūlymą"
    assert created_tasks[0]["discord_notified_at"] is not None
    assert callback_api_calls[0][0] == "answerCallbackQuery"


def test_discord_task_assignment_mentions_only_assignee(monkeypatch):
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord-bot-token")
    monkeypatch.setenv("DISCORD_TASK_CHANNEL_ID", "channel-123")
    requests = []

    class Response:
        def raise_for_status(self):
            return None

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def post(self, url, headers, json):
            requests.append((url, headers, json))
            return Response()

    monkeypatch.setattr(discord_service.httpx, "AsyncClient", lambda timeout: Client())
    asyncio.run(
        discord_service.send_task_assignment(
            {
                "id": 42,
                "title": "Paruošti ataskaitą",
                "details": "Įtraukti Q4 rezultatus",
                "due_at": "2030-12-31T15:00:00+02:00",
                "assignee_discord_user_id": "alex-discord",
                "requester_name": "Administrator",
            }
        )
    )

    assert len(requests) == 1
    url, headers, payload = requests[0]
    assert url.endswith("/channels/channel-123/messages")
    assert headers["Authorization"] == "Bot discord-bot-token"
    assert "<@alex-discord>" in payload["content"]
    assert "Įtraukti Q4 rezultatus" in payload["content"]
    assert payload["allowed_mentions"] == {
        "parse": [],
        "users": ["alex-discord"],
    }


def test_only_configured_admin_can_approve(client):
    intent = tasks.TaskIntent(
        action="create_task", title="Test", assignee_key="alex", due_at=None
    )
    token = tasks.create_pending_task("evt", 2, intent)
    with pytest.raises(PermissionError):
        tasks.approve_pending_task(token, 123)
    assert hub.fetchall("select * from tasks") == []


def test_gemini_returns_validated_task_query_intents(client, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("GEMINI_MODEL", "test-model")
    calls = []

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["model"] == "test-model"
            assert kwargs["config"].response_mime_type == "application/json"
            return type(
                "Response",
                (),
                {
                    "text": json.dumps({
                        "action": "query_tasks",
                        "assignee_key": "alex",
                        "status": "open",
                        "due_before": "2030-12-31T23:59:59+02:00",
                    })
                },
            )()

    class FakeClient:
        models = FakeModels()

    monkeypatch.setattr(tasks.genai, "Client", lambda api_key: FakeClient())
    intent = tasks.classify_message(
        "Kurios Alex užduotys iki metų pabaigos?",
        audio=b"voice-bytes",
        audio_mime_type="audio/ogg",
    )
    assert intent.action == "query_tasks"
    assert intent.assignee_key == "alex"
    assert tasks.parse_iso_datetime(intent.due_before) is not None
    assert calls[0]["contents"][1].inline_data.data == b"voice-bytes"


def test_voice_input_and_natural_language_task_queries(client, monkeypatch):
    alex = database.get_member_by_key("alex")
    hub.execute(
        "insert into tasks(title, assignee_id, created_by, due_at) values (?, ?, ?, ?)",
        ("Paruošti ataskaitą", alex["id"], alex["id"], "2030-12-31T15:00:00+02:00"),
    )
    sent = []

    async def capture_send(chat_id, text, reply_markup=None):
        sent.append((str(chat_id), text))

    monkeypatch.setattr(telegram, "send_message", capture_send)
    monkeypatch.setattr(
        telegram,
        "fetch_message_text",
        lambda message: asyncio.sleep(0, result=("", b"voice-bytes", "audio/ogg")),
    )
    seen = {}

    def fake_classify(text, audio=None, audio_mime_type="audio/ogg"):
        seen["audio"] = audio
        return tasks.TaskIntent(
            action="query_tasks",
            query_text="Ką Alexas dabar turi padaryti?",
            assignee_key="alex",
            status="open",
        )

    monkeypatch.setattr(telegram, "classify_message", fake_classify)
    questions = []
    monkeypatch.setattr(
        telegram,
        "summarize_tasks",
        lambda question, tasks: (
            questions.append(question) or f"Alex turi {len(tasks)} atvirą užduotį."
        ),
    )
    update = {
        "update_id": 20,
        "message": {
            "chat": {"id": 1001},
            "from": {"id": 1001},
            "voice": {"file_id": "file"},
        },
    }
    result = asyncio.run(telegram.handle_message(update))
    assert result["status"] == "query"
    assert seen["audio"] == b"voice-bytes"
    assert questions == ["Ką Alexas dabar turi padaryti?"]
    assert "1 atvirą užduotį" in sent[-1][1]


def test_reminders_are_idempotent_for_due_and_overdue_tasks(client, monkeypatch):
    fixed_now = hub.now_local().replace(microsecond=0)
    monkeypatch.setattr(hub, "now_local", lambda: fixed_now)
    alex = database.get_member_by_key("alex")
    for title, due in (
        ("Artėja terminas", fixed_now + timedelta(hours=2)),
        ("Pradelsta užduotis", fixed_now - timedelta(hours=2)),
    ):
        hub.execute(
            "insert into tasks(title, assignee_id, created_by, due_at) values (?, ?, ?, ?)",
            (title, alex["id"], alex["id"], due.isoformat()),
        )
    sent = []

    async def capture_send(chat_id, text, reply_markup=None):
        sent.append(text)

    async def capture_discord(task):
        return None

    monkeypatch.setattr(telegram, "send_message", capture_send)
    monkeypatch.setattr(hub, "send_task_assignment", capture_discord)
    first = asyncio.run(hub.internal_tick("internal-secret"))
    second = asyncio.run(hub.internal_tick("internal-secret"))
    assert first["sent"] == 2
    assert first["discord_sent"] == 2
    assert second["sent"] == 0
    assert second["discord_sent"] == 0
    assert any("Vėluoja" in message for message in sent)


def test_duplicate_telegram_updates_are_ignored(client, monkeypatch):
    sent = []

    async def capture_send(chat_id, text, reply_markup=None):
        sent.append(text)

    monkeypatch.setattr(telegram, "send_message", capture_send)
    update = {
        "update_id": 30,
        "message": {"chat": {"id": 555}, "from": {"id": 555}, "text": "hello"},
    }
    first = asyncio.run(telegram.handle_message(update))
    second = asyncio.run(telegram.handle_message(update))
    assert first["status"] == "error"
    assert second["status"] == "duplicate"
    assert len(sent) == 1


def test_telegram_webhook_requires_secret(client):
    response = client.post("/telegram/webhook", json={"update_id": 1})
    assert response.status_code == 401
    response = client.post(
        "/telegram/webhook",
        json={"update_id": 2},
        headers={"X-Telegram-Bot-Api-Secret-Token": "telegram-secret"},
    )
    assert response.status_code == 200


def test_discord_ping_and_signature_check(client, monkeypatch):
    signing_key = SigningKey.generate()
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", signing_key.verify_key.encode().hex())
    payload = {"type": 1}
    body = json.dumps(payload).encode()
    timestamp = "1711111111"
    signature = signing_key.sign(timestamp.encode() + b"." + body).signature.hex()
    response = client.post(
        "/discord/interactions",
        content=body,
        headers={
            "X-Signature-Ed25519": signature,
            "X-Signature-Timestamp": timestamp,
        },
    )
    assert response.json() == {"type": 1}
    bad = client.post(
        "/discord/interactions",
        content=body,
        headers={"X-Signature-Ed25519": "00", "X-Signature-Timestamp": timestamp},
    )
    assert bad.status_code == 401

    link_payload = {
        "type": 2,
        "id": "discord-link-attempt",
        "user": {"id": "777"},
        "data": {"name": "link", "options": [{"name": "code", "value": "anything"}]},
    }
    link_body = json.dumps(link_payload).encode()
    link_signature = signing_key.sign(
        timestamp.encode() + b"." + link_body
    ).signature.hex()
    response = client.post(
        "/discord/interactions",
        content=link_body,
        headers={
            "X-Signature-Ed25519": link_signature,
            "X-Signature-Timestamp": timestamp,
        },
    )
    assert "tik administratorius" in response.json()["data"]["content"].casefold()
    assert database.get_member_by_key("marta")["discord_user_id"] is None
