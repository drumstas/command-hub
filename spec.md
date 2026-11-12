# Command Hub: Task Management

## Scope

This application manages team tasks only.

- Receive freeform task requests by Telegram text or voice message.
- Use Gemini to extract a structured task proposal, including title, assignee,
  details, and deadline.
- Send each proposal to the Telegram administrator for approval or rejection.
- Insert an approved task into the database only after administrator approval.
- Accept natural-language questions about tasks in Telegram; use Gemini to
  produce validated filters, query the database in application code, and
  summarize only the matching results.
- List tasks, mark tasks done, and send due-soon and overdue reminders.
- Support Telegram as the full interface and Discord for account linking,
  listing tasks, and marking tasks done.

## Safety and authorization

- Gemini returns schema-constrained data; it never has database access or tools.
- Validate all assignee keys, dates, and query filters in application code.
- The database query layer uses fixed, parameterized SQL; model output never
  becomes SQL.
- New tasks remain pending until an administrator explicitly approves them.
- Only the configured administrator may approve or reject task proposals.
- Only a task's assignee or the administrator may mark it done.
- Telegram and Discord webhook signatures/secrets are verified.
- Duplicate webhook events are ignored.

## Runtime configuration

- Python 3.12, FastAPI, PostgreSQL/Neon, and SQLite for tests.
- Telegram Bot API webhooks and HTTP Bot API calls; no Telegram polling.
- Discord HTTP interactions; no gateway bot.
- `GEMINI_API_KEY` and a verified `GEMINI_MODEL` are supplied through the
  environment. Model identifiers are not hardcoded.
- The timezone for interpreting user dates is `Europe/Vilnius`.
- `/healthz` is a database-independent health endpoint.
- Authenticated `POST /internal/tick` sends task deadline reminders.

## Database

The schema contains team members, tasks, pending task proposals, consumed
one-time linking codes, processed webhook event IDs, and task audit records.
Migrations apply idempotently at startup.

## User workflows

1. Members link their chat account with a one-time code.
2. A member submits a task in Telegram using text or voice.
3. Gemini proposes task fields; the administrator reviews and approves or
   rejects the proposal.
4. On approval, application code inserts the task and notifies the requester
   and assignee where possible.
5. Members ask task questions in natural language; Gemini selects validated
   filters, application code retrieves matching task rows, and Gemini
   summarizes those rows.
6. Members use `/tasks [mine|all]` and `/done <id>` as direct commands.
