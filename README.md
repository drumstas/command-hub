# Command Hub

Command Hub is a small, stateless FastAPI service for task management and document drafting across Telegram and Discord.

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m pytest -q
uvicorn app.command_hub:app --reload
```

## Notes

- The service uses SQLite for local test runs when `DATABASE_URL` is not configured.
- Production uses Postgres via Neon and Render.
- No secrets are committed to the repository.
