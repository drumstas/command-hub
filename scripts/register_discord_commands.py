#!/usr/bin/env python3
"""Register Discord slash commands idempotently.

This is intentionally lightweight and repository-local; the app itself does not
require these commands to exist for tests to run.
"""
from __future__ import annotations

import json
import os
from urllib import request, error

APP_ID = os.getenv("DISCORD_APPLICATION_ID", "")
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")

COMMANDS = [
    {
        "name": "tasks",
        "description": "List tasks.",
        "options": [{"name": "scope", "description": "Task scope", "type": 3, "choices": [{"name": "mine", "value": "mine"}, {"name": "all", "value": "all"}]}],
    },
    {"name": "done", "description": "Mark a task as done.", "options": [{"name": "task_id", "description": "Task id", "type": 4}]},
    {"name": "link", "description": "Link your chat to your roster handle.", "options": [{"name": "code", "description": "Your shared code", "type": 3}]},
    {"name": "draft", "description": "Save a draft document.", "options": [{"name": "kind", "description": "Draft type", "type": 3, "choices": [{"name": "message", "value": "message"}, {"name": "quote", "value": "quote"}, {"name": "proforma", "value": "proforma"}]}, {"name": "text", "description": "Text to draft", "type": 3}]},
]

if not APP_ID or not BOT_TOKEN:
    raise SystemExit("DISCORD_APPLICATION_ID and DISCORD_BOT_TOKEN must be set.")

url = f"https://discord.com/api/v10/applications/{APP_ID}/commands"
req = request.Request(
    url,
    data=json.dumps(COMMANDS).encode("utf-8"),
    headers={
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
    },
    method="PUT",
)
try:
    with request.urlopen(req) as response:
        print(response.status)
        print(response.read().decode("utf-8"))
except error.HTTPError as exc:
    print(exc.read().decode("utf-8"))
    raise SystemExit(exc.code)
