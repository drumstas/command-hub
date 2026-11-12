I'll verify the current limits of Render, Neon and Gemini first, because your decision hinges on them.

## Verdict: right to simplify, but two of your three choices have problems

**1. Render free plan + Discord bot: this combination fails as you described it.**
- Render spins down a free web service after 15 minutes with no inbound traffic, and local files are lost whenever it restarts or spins down.
- A normal discord.py/discord.js bot holds an *outbound* gateway WebSocket, which Render doesn't count as activity.
- Render's own community replies confirm this: free web services spin down after 15 minutes without requests, and one user with a WebSocket-only bot reported it stopped working after a few hours until they moved to a paid plan.
- Background workers aren't part of the free tier either.
- Several hosting articles I found are sales pitches for competing hosts (Kuberns, livemy.app). Render's docs and staff replies carry the evidence here, not those.
- Wake-up takes about a minute, which is longer than Discord's 3-second slash-command deadline.

**The fix is to not use a gateway bot at all.** Use Discord HTTP interactions (slash commands and buttons delivered by webhook) and a Telegram webhook, so the app is purely request-driven. An external pinger keeps it warm and doubles as the scheduler for reminders. This costs you the ✅-reaction feature, because reactions need the gateway. Use a `/done` command or a button instead.

**2. Gemini on the "free" tier: probably not allowed for your use.**
- The API terms (effective March 2026) say only Paid Services may be used when making API clients available to users in the EEA, Switzerland or the UK.
- A 4-person Lithuanian team using your bot is plausibly that case. I'm quoting a third-party summary of Google's terms, so have your friend read the terms page.
- Enabling billing costs cents at this volume and also stops prompts and responses being used to improve Google products. Set a budget alert and a hard quota.
- Your summary says the 2.5 family was scheduled to shut down on 16 Oct 2026. I couldn't confirm which flash model is current, so the model ID must be configuration the agent verifies, not a hardcoded value.

**3. Neon free: fine, with one trap.**
- The free plan gives 100 CU-hours per month and 0.5 GB storage. Another source says 1 GB, so verify on Neon's pricing page.
- Free-plan compute suspends after 5 minutes idle, and you can't change that.
- If your keep-alive pinger touches the database every few minutes, compute never sleeps and you'll burn the allowance. The spec therefore has the keep-alive hit a route that skips the DB, with a DB-touching tick only every 15 minutes. The CU math is my estimate and rests on an assumed 0.25 CU minimum, so check it in the Neon console after a week.

**A scope deviation to approve: Drive storage.** You listed it as an initial requirement, but it's the riskiest part for a headless bot on a personal Gmail:
- Service accounts have zero storage quota, so uploads fail unless they target a Shared Drive. Shared Drives need Workspace.
- The alternative is user OAuth, but refresh tokens for external apps left in Testing status expire after seven days.
- So v1 stores documents in Neon and sends them as chat attachments. Drive is an optional phase with a decision tree.

**What the free setup can't give you:** reliability. Render may restart a free service at any time, and a missed ping means one failed first command. If the team starts to depend on this, the $7/month Starter instance removes the whole workaround layer. I'd decide that after a 2-week trial, not before.

---

# SPEC: Command Hub v1 (for an implementing agent)

## 0. Rules for the agent
- Implement exactly this scope. Don't add features.
- Never invent API fields or model IDs. Verify against official docs and say what you verified.
- No secrets in the repo. Every manual step goes into `docs/BOOTSTRAP.md`.
- Every phase ends with its acceptance tests passing. Stop and report if one can't pass.

## 1. Scope
**In:**
- Create and list tasks via chat, with assignee and deadline.
- Notify the assignee.
- Reminders before the deadline and when overdue.
- Mark tasks done.
- Draft messages, quotes and proformas as stored documents.
- Telegram and Discord are equal front-ends.

**Out:**
- Invoices (legal fields need an accountant).
- Auto-sending anything to customers.
- Gateway/reaction listening.
- Agent tool loops.
- Drive (optional Phase 5).

## 2. Architecture
One stateless FastAPI service on Render free (Frankfurt) and one Neon Postgres (eu-central-1). Gemini is called only for (a) parsing text into a structured intent and (b) drafting document text.

```
Telegram webhook ─┐
Discord interactions ─┼─> adapters ─> core (pure logic) ─> Neon
GitHub Actions cron ─┘ (/healthz 10 min, /internal/tick 15 min)   └─> Gemini (parse / draft only)
```

**Locked decisions (don't relitigate):**
- Python 3.12, FastAPI, httpx, psycopg 3 (connect per request, use Neon's pooled `-pooler` host, no idle pool), `google-genai`, pydantic v2, PyNaCl, pytest, ruff.
- The LLM never writes to the DB. It returns a JSON-schema-constrained intent. Code validates it, shows a **confirmation preview with Confirm/Cancel buttons**, and only then runs fixed functions.
- Assignees are an enum of roster keys supplied in the prompt. The LLM never sees or emits Discord/Telegram IDs.
- Due dates: the LLM returns ISO 8601 or null. Code injects the current datetime in `Europe/Vilnius` into the prompt and rejects unparseable dates by asking the user.
- Idempotency: every Telegram `update_id` and Discord interaction `id` is recorded. Duplicates are ignored.
- Discord: verify the Ed25519 signature on every request, answer PING, and **defer immediately** (type 5) for anything that calls Gemini, then PATCH the follow-up. Telegram: verify `X-Telegram-Bot-Api-Secret-Token`.
- Prompt-injection posture: pasted customer text is untrusted. Because the LLM has no tools and nothing executes without a human Confirm, the blast radius is a bad draft.

## 3. Data model (`migrations/0001_init.sql`, applied on startup under an advisory lock)
```sql
create table members(
  id serial primary key, key text unique not null, display_name text not null,
  role text not null check (role in ('owner','member')),
  telegram_chat_id bigint unique, discord_user_id text unique,
  link_code text, preferred_channel text check (preferred_channel in ('telegram','discord')));
create table tasks(
  id serial primary key, title text not null, details text,
  assignee_id int not null references members(id),
  created_by int not null references members(id),
  due_at timestamptz, status text not null default 'open' check (status in ('open','done','cancelled')),
  done_at timestamptz, reminded_at timestamptz, overdue_notified_on date,
  created_at timestamptz not null default now());
create table documents(
  id serial primary key, kind text not null check (kind in ('message','quote','proforma')),
  title text not null, body_md text not null, status text not null default 'draft'
    check (status in ('draft','approved','void')),
  number text unique,          -- assigned by DB sequence at approval, never by the LLM
  created_by int references members(id), approved_by int references members(id),
  created_at timestamptz not null default now());
create table processed_events(source text, event_id text, at timestamptz default now(), primary key(source,event_id));
create table audit_log(id bigserial primary key, at timestamptz default now(), member_id int, action text, payload jsonb);
create table llm_calls(id bigserial primary key, at timestamptz default now(), purpose text, model text, in_tokens int, out_tokens int, ok boolean);
```
Authorization: unknown chat IDs and Discord users get no response beyond "not linked". Members link with `/link CODE`, and the owner generates the code. Only `owner` may approve documents or cancel others' tasks. Only the assignee or the owner may mark a task done.

## 4. Commands (identical semantics on both platforms)
- Free text → parse → preview → Confirm. Example: "task for Jonas: send offer to Acme by Friday 15:00".
- `/tasks [mine|all]`, `/done <id>`, `/link <code>`
- `/draft <message|quote|proforma> <free text>` → stored as `draft`, shown with a "DRAFT, not a tax document" banner, and sent as a `.md` and a PDF attachment.
- `/approve <doc id>` (owner only) assigns the number from a sequence. Nothing is ever sent externally.

## 5. Scheduling
- `/healthz` never touches the DB. GitHub Actions pings it every 10 minutes (`.github/workflows/keepalive.yml`, in the repo, so it's reproducible).
- `/internal/tick` is authenticated with a secret header and runs every 15 minutes. It reminds for tasks due within 24h when `reminded_at` is null, and sends one overdue notice per day. It's idempotent.
- GitHub scheduled runs are best-effort and can be delayed. Document redundancy via cron-job.org in `BOOTSTRAP.md`.
- Notification order: preferred channel DM, then fallback to a channel mention. Telegram bots can only message users who have started the bot, which is why `/link` is required.

## 6. IaC and the honest limits
**Committed to the repo:**
- `render.yaml` (Blueprint): `type: web`, `plan: free`, `region: frankfurt`, `healthCheckPath: /healthz`, and all secrets as `sync: false`
- `migrations/`
- `scripts/register_discord_commands.py` and `scripts/set_telegram_webhook.py` (idempotent)
- `.env.example`
- `Makefile` targets `bootstrap`, `deploy-check`, `smoke`, `test`
- GitHub workflow for keepalive
- Optional `infra/` Terraform for the Neon project only if the provider is verified current

**Not automatable, so write exact click-paths in `docs/BOOTSTRAP.md`:**
- Creating the Discord application and Telegram bot (BotFather)
- Enabling Gemini billing and setting a budget alert
- Creating the Neon account
- Entering secrets into Render
- Adding the Discord Interactions Endpoint URL

Tell your friend plainly that "IaC" here means reproducible config plus a checklist, not one-command provisioning.

## 7. Phases and acceptance tests
1. **Skeleton and infra.** FastAPI, `/healthz`, migrations, Render deploy, Neon connection. *Accept:* the deployed `/healthz` returns 200, and migrations are idempotent across two restarts.
2. **Telegram core.** Webhook, secret check, linking, task create/list/done with confirm buttons, Gemini parsing with a fake client in tests. *Accept:* unit tests cover parsing edge cases ("Friday", no date, unknown assignee), duplicate update ignored, and unlinked user rejected.
3. **Discord.** Signature verification, PING, deferred slash commands, button interactions, DMs. *Accept:* an invalid signature returns 401, and a cold-start test shows a deferred response in under 3s locally.
4. **Reminders and documents.** Tick endpoint, `/draft`, `/approve`, attachments. *Accept:* the tick is idempotent when run twice, and the number is assigned only on approval.
5. **Optional Drive.** First ask whether the friend has Workspace. If yes, use a Shared Drive plus a service account. If personal Gmail, use user OAuth with the consent screen published to production, and verify refresh-token behaviour over more than 7 days before calling it done. Otherwise skip.
6. **Hardening.** Error handler that messages the owner, `llm_calls` cost report command, and a README with a 2-week trial checklist (missed pings, Neon CU-hours, Gemini spend).

## 8. Decisions I need from you before the agent starts
- Does the friend have Google Workspace (affects Drive)?
- Are you OK with the confirm-before-write step? I think it's non-negotiable given that assignees get notified.
- Do you accept the keep-alive approach for a 2-week trial, with a $7 upgrade as the exit?
