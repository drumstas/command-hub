from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from app import command_hub as hub


@pytest.fixture
def client(tmp_path, monkeypatch):
    database = tmp_path / "command_hub.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{database}")
    monkeypatch.setenv("TELEGRAM_SECRET", "telegram-secret")
    monkeypatch.setenv("INTERNAL_SECRET", "internal-secret")
    monkeypatch.setenv("OWNER_TELEGRAM_CHAT_ID", "900")
    monkeypatch.setenv("OWNER_TELEGRAM_USER_ID", "900")
    monkeypatch.setenv("TEAM_ROSTER", json.dumps([
        {"key": "alex", "display_name": "Alex", "telegram_chat_id": 200},
        {"key": "jonas", "display_name": "Jonas", "telegram_chat_id": 1001},
        {"key": "marta", "display_name": "Marta"},
    ]))
    monkeypatch.setenv("LINK_CODES", json.dumps({
        "alex-code": {"key": "alex", "display_name": "Alex"},
        "jonas-code": {"key": "jonas", "display_name": "Jonas"},
        "marta-code": {"key": "marta", "display_name": "Marta"},
        "marta-discord-code": {"key": "marta", "display_name": "Marta"},
    }))
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
    assert "documents" not in tables
    assert "llm_calls" not in tables


def test_migration_removes_non_task_tables(tmp_path, monkeypatch):
    database = tmp_path / "legacy.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("create table schema_migrations(version integer primary key)")
    connection.execute("insert into schema_migrations(version) values (1), (2)")
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
    assert "documents" not in remaining
    assert "llm_calls" not in remaining
    connection.close()


def test_link_code_cannot_be_used_to_take_over_member(client):
    linked = hub.link_member("telegram", 300, "alex-code", "Alex")
    assert linked is None
    original = hub.get_member_by_key("alex")
    assert original["telegram_chat_id"] == 200

    code = hub.link_member("telegram", 301, "marta-code", "Marta")
    assert code["key"] == "marta"
    assert hub.link_member("telegram", 302, "marta-code", "Marta") is None
    discord_link = hub.link_member(
        "discord", "marta-discord", "marta-discord-code", "Marta"
    )
    assert discord_link["telegram_chat_id"] == 301
    assert discord_link["discord_user_id"] == "marta-discord"


def test_task_is_only_persisted_after_admin_approval(client, monkeypatch):
    sent = []

    async def capture_send(chat_id, text, reply_markup=None):
        sent.append((str(chat_id), text, reply_markup))

    monkeypatch.setattr(hub, "send_telegram_message", capture_send)
    monkeypatch.setattr(
        hub,
        "classify_message",
        lambda text, audio=None, audio_mime_type="audio/ogg": hub.TaskIntent(
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
    result = asyncio.run(hub.handle_telegram_message(update))
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

    async def capture_api(method, data):
        callback_api_calls.append((method, data))
        return {"ok": True}

    monkeypatch.setattr(hub, "telegram_api", capture_api)
    approved = asyncio.run(hub.handle_telegram_callback(callback))
    assert approved["status"] == "approve"
    tasks = hub.fetchall("select * from tasks")
    assert len(tasks) == 1
    assert tasks[0]["status"] == "open"
    assert hub.fetchall("select * from pending_tasks") == []
    assert any(destination == "200" for destination, *_ in sent)
    assert callback_api_calls[0][0] == "answerCallbackQuery"


def test_only_configured_admin_can_approve(client):
    intent = hub.TaskIntent(
        action="create_task", title="Test", assignee_key="alex", due_at=None
    )
    token = hub.create_pending_task("evt", 2, intent)
    with pytest.raises(PermissionError):
        hub.approve_pending_task(token, 123)
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

    monkeypatch.setattr(hub.genai, "Client", lambda api_key: FakeClient())
    intent = hub.classify_message(
        "Kurios Alex užduotys iki metų pabaigos?",
        audio=b"voice-bytes",
        audio_mime_type="audio/ogg",
    )
    assert intent.action == "query_tasks"
    assert intent.assignee_key == "alex"
    assert hub.parse_iso_datetime(intent.due_before) is not None
    assert calls[0]["contents"][1].inline_data.data == b"voice-bytes"


def test_voice_input_and_natural_language_task_queries(client, monkeypatch):
    alex = hub.get_member_by_key("alex")
    hub.execute(
        "insert into tasks(title, assignee_id, created_by, due_at) values (?, ?, ?, ?)",
        ("Paruošti ataskaitą", alex["id"], alex["id"], "2030-12-31T15:00:00+02:00"),
    )
    sent = []

    async def capture_send(chat_id, text, reply_markup=None):
        sent.append((str(chat_id), text))

    monkeypatch.setattr(hub, "send_telegram_message", capture_send)
    monkeypatch.setattr(
        hub,
        "fetch_message_text",
        lambda message: asyncio.sleep(0, result=("", b"voice-bytes", "audio/ogg")),
    )
    seen = {}

    def fake_classify(text, audio=None, audio_mime_type="audio/ogg"):
        seen["audio"] = audio
        return hub.TaskIntent(
            action="query_tasks",
            query_text="Ką Alexas dabar turi padaryti?",
            assignee_key="alex",
            status="open",
        )

    monkeypatch.setattr(hub, "classify_message", fake_classify)
    questions = []
    monkeypatch.setattr(
        hub,
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
    result = asyncio.run(hub.handle_telegram_message(update))
    assert result["status"] == "query"
    assert seen["audio"] == b"voice-bytes"
    assert questions == ["Ką Alexas dabar turi padaryti?"]
    assert "1 atvirą užduotį" in sent[-1][1]


def test_reminders_are_idempotent_for_due_and_overdue_tasks(client, monkeypatch):
    fixed_now = hub.now_local().replace(microsecond=0)
    monkeypatch.setattr(hub, "now_local", lambda: fixed_now)
    alex = hub.get_member_by_key("alex")
    for title, due in (
        ("Artėja terminas", fixed_now + hub.timedelta(hours=2)),
        ("Pradelsta užduotis", fixed_now - hub.timedelta(hours=2)),
    ):
        hub.execute(
            "insert into tasks(title, assignee_id, created_by, due_at) values (?, ?, ?, ?)",
            (title, alex["id"], alex["id"], due.isoformat()),
        )
    sent = []

    async def capture_send(chat_id, text, reply_markup=None):
        sent.append(text)

    monkeypatch.setattr(hub, "send_telegram_message", capture_send)
    first = asyncio.run(hub.internal_tick("internal-secret"))
    second = asyncio.run(hub.internal_tick("internal-secret"))
    assert first["sent"] == 2
    assert second["sent"] == 0
    assert any("Vėluoja" in message for message in sent)


def test_duplicate_telegram_updates_are_ignored(client, monkeypatch):
    sent = []

    async def capture_send(chat_id, text, reply_markup=None):
        sent.append(text)

    monkeypatch.setattr(hub, "send_telegram_message", capture_send)
    update = {
        "update_id": 30,
        "message": {"chat": {"id": 555}, "from": {"id": 555}, "text": "hello"},
    }
    first = asyncio.run(hub.handle_telegram_message(update))
    second = asyncio.run(hub.handle_telegram_message(update))
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
