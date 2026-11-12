from __future__ import annotations

import os
from typing import Any

import httpx

DISCORD_API_URL = "https://discord.com/api/v10"
DISCORD_MESSAGE_LIMIT = 2000


def _chunks(content: str) -> list[str]:
    return [
        content[index : index + DISCORD_MESSAGE_LIMIT]
        for index in range(0, len(content), DISCORD_MESSAGE_LIMIT)
    ]


async def send_task_assignment(task: dict[str, Any]) -> None:
    bot_token = os.getenv("DISCORD_BOT_TOKEN")
    channel_id = os.getenv("DISCORD_TASK_CHANNEL_ID")
    discord_user_id = task.get("assignee_discord_user_id")
    if not bot_token or not channel_id:
        raise RuntimeError(
            "DISCORD_BOT_TOKEN and DISCORD_TASK_CHANNEL_ID must be configured."
        )
    if not discord_user_id:
        raise RuntimeError("The task assignee has not linked a Discord account.")

    content = (
        f"Task #{task['id']} assigned to <@{discord_user_id}>\n"
        f"Title: {task['title']}\n"
        f"Details: {task.get('details') or 'None'}\n"
        f"Due: {task.get('due_at') or 'No deadline'}\n"
        f"Requested by: {task.get('requester_name') or 'Administrator'}"
    )
    headers = {"Authorization": f"Bot {bot_token}"}
    payload_base = {
        "allowed_mentions": {"parse": [], "users": [str(discord_user_id)]}
    }

    async with httpx.AsyncClient(timeout=20) as client:
        for chunk in _chunks(content):
            response = await client.post(
                f"{DISCORD_API_URL}/channels/{channel_id}/messages",
                headers=headers,
                json={**payload_base, "content": chunk},
            )
            response.raise_for_status()
