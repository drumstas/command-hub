# Deployment setup

The application handles task management only.

## Required configuration

Copy `.env.example` to `.env` for local development, or enter these values in
Render's environment settings:

- `DATABASE_URL`: pooled Neon Postgres connection string in production.
- `TELEGRAM_BOT_TOKEN`: token created with BotFather.
- `TELEGRAM_SECRET`: Telegram webhook secret token; use a random value allowed
  by Telegram's secret-token format.
- `OWNER_TELEGRAM_CHAT_ID`: administrator's private Telegram chat ID, used for
  approval prompts and linked to the built-in `owner` member.
- `OWNER_TELEGRAM_USER_ID`: administrator's Telegram user ID, checked when
  approving/rejecting tasks. Set both Telegram IDs; in a private chat they
  usually have the same value.
- `TEAM_ROSTER`: JSON array of task members, including the administrator under
  the reserved key `owner`. For example:
  `[{"key":"owner","display_name":"Administrator"},{"key":"alex","display_name":"Alex"}]`.
  This updates the built-in owner member rather than creating a second admin.
- `LINK_CODES`: JSON object mapping one-time codes to members, e.g.
  `{"admin-discord-code":{"key":"owner","display_name":"Administrator"},"alex-discord-code":{"key":"alex","display_name":"Alex"}}`.
  Share each code privately with its intended member. A code is consumed on
  first successful linking and cannot be used to bind a different account. Use
  separate codes if a member needs to link both Telegram and Discord. The
  administrator's Telegram account is linked by `OWNER_TELEGRAM_CHAT_ID`;
  use the `owner` Discord link code to connect their Discord account, or put
  their Discord user ID in the `owner` roster entry instead.
- `GEMINI_API_KEY` and `GEMINI_MODEL`: API key and model name verified against
  the current Google AI documentation. There is deliberately no hardcoded
  fallback model.
- `INTERNAL_SECRET`: secret for the scheduled reminder endpoint.

All JSON environment values must be valid JSON. Do not commit credentials or
real Telegram IDs in this repository.

## Telegram bot and webhook

1. Create a bot with BotFather and set `TELEGRAM_BOT_TOKEN`.
2. Set the administrator chat and user IDs. Include the administrator in
   `TEAM_ROSTER` with `key: "owner"` so Gemini can assign tasks to them too.
3. Configure unique link codes. The administrator should send `/link ADMIN_CODE`
   in Discord if their Discord ID is not already set in the roster.
4. Deploy the service and set `APP_PUBLIC_URL` to its HTTPS URL.
5. Run `python3 scripts/set_telegram_webhook.py`. It registers
   `/telegram/webhook`, the configured secret token, and the message/callback
   update types.
6. Ask each member to start the bot and send `/link THEIR_CODE`.
7. Test a task proposal and approve/reject buttons with the administrator.

Voice messages are downloaded from Telegram and sent to the configured Gemini
model for task extraction. Text and voice use the same approval flow.

## Discord (task commands only)

Discord currently supports the registered `/tasks`, `/done`, and `/link`
commands. Configure `DISCORD_PUBLIC_KEY`, `DISCORD_APPLICATION_ID`, and
`DISCORD_BOT_TOKEN`, plus `DISCORD_TASK_CHANNEL_ID` for approved task
assignments, then run:

```bash
python3 scripts/register_discord_commands.py
```

Set the application's interactions endpoint to
`https://YOUR-SERVICE/discord/interactions`. Discord task creation and
natural-language/voice queries are not enabled in this version; use Telegram
for those workflows. The bot needs permission to view and send messages in the
configured task channel. Each assignee must have a linked Discord account
(through a one-time `/link` code) or a `discord_user_id` in `TEAM_ROSTER`.
When an administrator approves a Telegram task, the bot posts its title,
details, deadline, and requester in that channel and mentions the assignee.
Failed Discord deliveries remain pending and are retried by `/internal/tick`.

## Database and reminders

The app applies idempotent migrations on startup. Migration `0002` creates
task-approval storage.

Configure a scheduler to call `POST /internal/tick` with the
`X-Internal-Secret` header set to `INTERNAL_SECRET` every 15 minutes. This
endpoint sends Telegram due-soon and overdue reminders. `/healthz` is a
separate lightweight health check and does not run reminders.

For a Render deployment, use `render.yaml` and add all secret values in the
Render dashboard. GitHub Actions scheduled jobs are best-effort; configure an
independent scheduler as a fallback if reminder timing matters.

## Local checks

```bash
python3 -m pytest -q
python3 -m uvicorn app.command_hub:app --reload
curl http://localhost:8000/healthz
```
