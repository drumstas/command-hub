from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

from app.command_hub import (
    approve_document,
    apply_migrations,
    create_document,
    ensure_member_for_link,
    ensure_seed_members,
    handle_telegram_message,
    parse_task_intent,
    tick_reminders,
    app,
)


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "command_hub.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("TELEGRAM_SECRET", "telegram-secret")
    monkeypatch.setenv("INTERNAL_SECRET", "internal-secret")
    monkeypatch.setenv("OWNER_CODE", "owner")
    apply_migrations()
    ensure_seed_members()
    with TestClient(app) as client:
        yield client


def test_healthz(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_migrations_are_idempotent(client):
    apply_migrations()
    apply_migrations()
    response = client.get("/healthz")
    assert response.status_code == 200


def test_telegram_link_and_parse_edge_cases(client):
    ensure_member_for_link("telegram", "1001", "jonas", "Jonas")

    update = {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "chat": {"id": 1001},
            "from": {"id": 1001, "first_name": "Jonas"},
            "text": "/link jonas",
        },
    }
    linked = handle_telegram_message(update)
    assert linked["status"] == "linked"

    prompt = "task for Jonas: send offer to Acme by Friday 15:00"
    parsed = parse_task_intent(prompt, {"jonas": "Jonas"})
    assert parsed["assignee_key"] == "jonas"
    assert parsed["title"].startswith("send offer to Acme")
    assert parsed["due_at"] is not None

    unlinked = handle_telegram_message({
        "update_id": 2,
        "message": {"chat": {"id": 9999}, "from": {"id": 9999, "first_name": "Unknown"}, "text": "task for Jonas: send offer to Acme by Friday 15:00"},
    })
    assert unlinked["status"] == "error"
    assert unlinked["message"] == "not linked"

    duplicate = handle_telegram_message({
        "update_id": 1,
        "message": {"chat": {"id": 1001}, "from": {"id": 1001, "first_name": "Jonas"}, "text": "task for Jonas: send offer to Acme by Friday 15:00"},
    })
    assert duplicate["status"] == "duplicate"


def test_discord_signature_and_defer(client):
    signing_key = SigningKey.generate()
    public_key_hex = signing_key.verify_key.encode().hex()
    os.environ["DISCORD_PUBLIC_KEY"] = public_key_hex
    payload = {"type": 1}
    timestamp = "1711111111"
    signed = signing_key.sign(f"{timestamp}.{json.dumps(payload)}".encode("utf-8")).signature.hex()
    response = client.post(
        "/discord/interactions",
        content=json.dumps(payload),
        headers={
            "X-Signature-Ed25519": signed,
            "X-Signature-Timestamp": timestamp,
        },
    )
    assert response.status_code == 200
    assert response.json()["type"] == 1

    bad_response = client.post(
        "/discord/interactions",
        content=json.dumps({"type": 2}),
        headers={"X-Signature-Ed25519": "00", "X-Signature-Timestamp": "0"},
    )
    assert bad_response.status_code == 401

    good_payload = {"type": 2, "data": {"name": "tasks", "options": [{"name": "scope", "value": "mine"}]}}
    signed = signing_key.sign(f"{timestamp}.{json.dumps(good_payload)}".encode("utf-8")).signature.hex()
    deferred = client.post(
        "/discord/interactions",
        content=json.dumps(good_payload),
        headers={
            "X-Signature-Ed25519": signed,
            "X-Signature-Timestamp": timestamp,
        },
    )
    assert deferred.status_code == 200
    assert deferred.json()["type"] == 5


def test_tick_is_idempotent_and_documents_approve(client):
    ensure_member_for_link("telegram", "2002", "marta", "Marta")
    task = {"title": "Prepare handover", "assignee_key": "marta", "details": "Prepare handover", "due_at": "2030-12-31T15:00:00+02:00"}
    from app.command_hub import create_task_from_preview_data

    created = create_task_from_preview_data(1, task)
    assert created["id"] > 0

    first = tick_reminders()
    assert isinstance(first["sent"], list)

    second = tick_reminders()
    assert second["sent"] == []

    doc = create_document("message", "Customer message", "Hello team", 1)
    assert doc["status"] == "draft"
    assert doc["number"] is None
    approved = approve_document(doc["id"], 1)
    assert approved["status"] == "approved"
    assert approved["number"] == f"CMD-{doc['id']:04d}"
