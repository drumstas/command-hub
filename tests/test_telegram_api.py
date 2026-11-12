from __future__ import annotations

import asyncio

import pytest

from app.services import telegram


class FakeResponse:
    def __init__(self, payload, error=None):
        self.payload = payload
        self.error = error

    def raise_for_status(self):
        if self.error:
            raise self.error

    def json(self):
        return self.payload


class FakeAsyncClient:
    def __init__(self, response, requests):
        self.response = response
        self.requests = requests

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def post(self, url, json):
        self.requests.append((url, json))
        return self.response


def test_telegram_api_posts_to_bot_method(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    requests = []
    monkeypatch.setattr(
        telegram.httpx,
        "AsyncClient",
        lambda timeout: FakeAsyncClient(FakeResponse({"ok": True}), requests),
    )

    result = asyncio.run(telegram.api("sendMessage", {"chat_id": 42, "text": "Hi"}))

    assert result == {"ok": True}
    assert requests == [
        ("https://api.telegram.org/bottest-token/sendMessage", {"chat_id": 42, "text": "Hi"})
    ]


def test_telegram_api_rejects_missing_token(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
        asyncio.run(telegram.api("sendMessage", {}))


def test_telegram_api_reports_unsuccessful_api_response(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setattr(
        telegram.httpx,
        "AsyncClient",
        lambda timeout: FakeAsyncClient(FakeResponse({"ok": False}), []),
    )

    with pytest.raises(RuntimeError, match="sendMessage"):
        asyncio.run(telegram.api("sendMessage", {}))


def test_telegram_api_propagates_http_errors(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    http_error = RuntimeError("HTTP request failed")
    monkeypatch.setattr(
        telegram.httpx,
        "AsyncClient",
        lambda timeout: FakeAsyncClient(
            FakeResponse({"ok": True}, error=http_error), []
        ),
    )

    with pytest.raises(RuntimeError, match="HTTP request failed"):
        asyncio.run(telegram.api("sendMessage", {}))
