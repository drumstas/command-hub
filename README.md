# Command Hub

Command Hub is a task-only assistant for Telegram, with a limited Discord task
command interface. Gemini turns Telegram text or voice into a structured task
proposal, and answers natural-language questions using task records fetched by
fixed, parameterized application queries. Gemini cannot access or write to the
database. Proposed tasks are persisted only after an administrator approves
them in Telegram.

On approval, the task is also posted to the configured Discord task channel,
where the assigned teammate is mentioned. See
[docs/BOOTSTRAP.md](docs/BOOTSTRAP.md) for Discord channel setup.

## Telegram features

- Submit a task as freeform text or a voice message.
- Review proposed title, assignee, and deadline; an administrator approves or
  rejects it using Telegram buttons.
- Ask questions such as “Which tasks are due by Friday?” or “What is Alex
  working on?” Gemini selects safe query filters, the app fetches matching
  tasks, then Gemini summarizes only those results.
- Use `/tasks [mine|all]` to list tasks and `/done ID` to complete one.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python3 -m pip install -r requirements.txt
cp .env.example .env
python3 -m pytest -q
python3 -m uvicorn app.command_hub:app --reload
```

Configure the Gemini model, Telegram bot, administrator, and roster before
using the bot. The administrator assigns member IDs through Telegram `/link`;
members cannot link themselves. See [docs/BOOTSTRAP.md](docs/BOOTSTRAP.md).

## Important

- Tasks are not created until an administrator presses **Patvirtinti**.
- Telegram query access is available only to linked members.
- Production uses Postgres; local tests use SQLite.
- Do not commit `.env` or any real credentials.
