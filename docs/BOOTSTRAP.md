# Bootstrap checklist

This project intentionally avoids storing any secret values in the repository. Use a local `.env` copy of `.env.example` and inject real values in your deployment system.

## 1. Local env
1. Copy `.env.example` to `.env`.
2. Fill in `DATABASE_URL`, `TELEGRAM_SECRET`, `DISCORD_PUBLIC_KEY`, `DISCORD_APPLICATION_ID`, `DISCORD_BOT_TOKEN`, `INTERNAL_SECRET`, `GEMINI_API_KEY`, `GEMINI_MODEL`, and `OWNER_CODE`.
3. Make sure `GEMINI_MODEL` matches the current model you verified on Google's docs for the API you intend to call.
4. For local tests, a SQLite database is used automatically when `DATABASE_URL` is unset.

## 2. Discord setup
1. Create a Discord application in the developer portal.
2. Add a bot user and copy the bot token to `DISCORD_BOT_TOKEN`.
3. Add the application id to `DISCORD_APPLICATION_ID`.
4. Register slash commands with `python scripts/register_discord_commands.py`.
5. Add the public URL of the Render service to the Discord interactions endpoint config.

## 3. Telegram setup
1. Create a Telegram bot with BotFather.
2. Set the bot token in `TELEGRAM_BOT_TOKEN`.
3. Set `APP_PUBLIC_URL` to the public Render URL.
4. Run `python scripts/set_telegram_webhook.py` to register the webhook.
5. The header `X-Telegram-Bot-Api-Secret-Token` must match `TELEGRAM_SECRET`.

## 4. Neon and Render setup
1. Create a Neon project in `eu-central-1`.
2. Copy the pooled Postgres connection string into `DATABASE_URL`.
3. Deploy the app on Render using `render.yaml`.
4. Add all environment variables in Render, leaving `sync: false` so they are not committed.
5. Ensure the health check is `/healthz`.

## 5. Gemini billing and quotas
1. Enable billing for the Gemini API and set a hard quota / alert.
2. Verify the model name from the current Google docs before setting `GEMINI_MODEL`.
3. Keep the app in "LLM parses only" mode; the DB is never written by the model.

## 6. Production hardening
1. Configure a GitHub Actions cron to hit `/healthz` every 10 minutes.
2. Configure an external cron-job.org fallback as a redundancy layer if the GitHub job is delayed.
3. Watch Render free-tier restarts and Neon free-tier idle suspension during the first 2 weeks.
4. If reliability becomes a requirement, move to the $7/month Render Starter plan.

## 7. Manual validation
- `curl http://localhost:8000/healthz`
- `python -m pytest -q`
- `curl -fsS https://your-render-url/healthz`
