#!/usr/bin/env python3
"""Set the Telegram webhook to the configured app URL."""
from __future__ import annotations

import json
import os
from pathlib import Path
from urllib import parse, request

from dotenv import load_dotenv

APP_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(APP_ROOT / ".env")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_URL = os.getenv("APP_PUBLIC_URL", "")
WEBHOOK_SECRET = os.getenv("TELEGRAM_SECRET", "")

if not BOT_TOKEN or not WEBHOOK_URL or not WEBHOOK_SECRET:
    raise SystemExit("TELEGRAM_BOT_TOKEN, APP_PUBLIC_URL and TELEGRAM_SECRET must be set.")

payload = {
    "url": WEBHOOK_URL.rstrip("/") + "/telegram/webhook",
    "secret_token": WEBHOOK_SECRET,
    "allowed_updates": json.dumps(["message", "callback_query"]),
}
url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
req = request.Request(
    url,
    data=parse.urlencode(payload).encode(),
    headers={"Content-Type": "application/x-www-form-urlencoded"},
    method="POST",
)
with request.urlopen(req) as response:
    print(response.status)
    print(response.read().decode("utf-8"))
