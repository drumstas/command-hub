#!/usr/bin/env python3
"""Set the Telegram webhook to the configured app URL."""
from __future__ import annotations

import os
from urllib import parse, request

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
WEBHOOK_URL = os.getenv("APP_PUBLIC_URL", "")

if not BOT_TOKEN or not WEBHOOK_URL:
    raise SystemExit("TELEGRAM_BOT_TOKEN and APP_PUBLIC_URL must be set.")

payload = {"url": WEBHOOK_URL.rstrip("/") + "/telegram/webhook"}
url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
params = parse.urlencode({"url": payload["url"]})
req = request.Request(url + "?" + params, method="POST")
with request.urlopen(req) as response:
    print(response.status)
    print(response.read().decode("utf-8"))
