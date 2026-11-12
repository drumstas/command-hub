# Manual acceptance testing

Run these checks against a staging deployment or a database where test tasks
are acceptable. Task completion is persistent; there is no undo command. Use
test accounts and avoid real customer or confidential data.

The app is intentionally task-only, so it has no separate document or general
chat workflows. These scenarios also check deployment, database, webhook, and
authentication behavior outside the task flows.

## Before testing

- Complete [deployment setup](./BOOTSTRAP.md), deploy the service, and verify
  `GET /healthz` returns `{"status":"ok"}`.
- To test first-time database setup, use a new empty staging database. Do not
  reset or drop a database containing data you need.
- Configure the Telegram bot, Gemini API key and model, administrator IDs,
  roster, Discord application and bot, Discord task channel, and
  `INTERNAL_SECRET`.
- Register Discord commands with `python3 scripts/register_discord_commands.py`
  and set the Discord interactions endpoint to
  `https://YOUR-SERVICE/discord/interactions`.
- Register the Telegram webhook with `python3 scripts/set_telegram_webhook.py`.
- Have each test member start a private conversation with the Telegram bot so
  it can send them notifications. Collect account IDs and assign them using
  the administrator's Telegram `/link` command. At minimum, the assignee needs
  a linked Discord account for task approval and a linked Telegram account for
  reminders. To test Discord commands as the administrator, link the owner's
  Discord ID as well.
- Keep a note of each created task ID. The examples below assume Alex and
  another linked member are in `TEAM_ROSTER`.

## Scenarios

### 1. Health and webhook authentication

1. Request `GET https://YOUR-SERVICE/healthz`.
2. Send a Telegram webhook request without the configured secret header:

   ```bash
   curl -i -X POST https://YOUR-SERVICE/telegram/webhook \
     -H 'Content-Type: application/json' \
     -d '{"update_id":900001}'
   ```

Expected: health returns HTTP 200 and `{"status":"ok"}`; the webhook request
returns HTTP 401. Telegram's real webhook, registered by the setup script,
should continue to process updates normally.

### 2. Fresh database initialization and restart persistence

1. Deploy against the empty staging database and wait for application startup.
2. Check deployment logs for successful startup and no migration errors.
3. If you have database read access, verify PostgreSQL recorded schema
   versions 1 and 2, and that the initial tasks schema includes the Discord
   notification column:

   ```sql
   select version from schema_migrations order by version;
   select column_name from information_schema.columns
   where table_name = 'tasks' and column_name = 'discord_notified_at';
   ```

4. Create and approve a labeled UAT task using scenario 5, then restart or
   redeploy the service.
5. Run `/tasks all` and confirm the UAT task is still present.

Expected: startup initializes the schema without a manual migration command;
versions 1 and 2 are recorded, and `discord_notified_at` exists. Restarting the
service preserves task data and does not fail migrations.

### 3. Discord interaction endpoint authentication

1. In the Discord Developer Portal, configure the interactions endpoint as
   `https://YOUR-SERVICE/discord/interactions` and save it.
2. Confirm Discord accepts endpoint verification.
3. Send a request with an invalid signature:

   ```bash
   curl -i -X POST https://YOUR-SERVICE/discord/interactions \
     -H 'Content-Type: application/json' \
     -H 'X-Signature-Ed25519: 00' \
     -H 'X-Signature-Timestamp: 1711111111' \
     -d '{}'
   ```

Expected: Discord accepts the endpoint verification, while the invalid request
returns HTTP 401 and does not execute a command.

### 4. Administrator-managed account linking

1. As the administrator, send `/link alex discord 123456789012345678` using
   Alex's actual numeric Discord user ID.
2. Repeat the command with the same ID for a different roster key.
3. Ask a non-administrator to send a `/link` command.

Expected: step 1 confirms the assignment; steps 2 and 3 are rejected, and
existing account assignments remain unchanged. Repeat step 1 with
`telegram` and Alex's Telegram user ID if it has not already been assigned.

### 5. Text proposal, administrator approval, and Discord assignment

1. From a linked Telegram account, send a clear request, for example:
   `Assign Alex: prepare the UAT launch checklist by Friday at 15:00. Include
   rollback steps.`
2. Check the administrator's Telegram chat for a proposal with the title,
   assignee, deadline, and details.
3. Before approval, check `/tasks all` from Telegram.
4. Approve the proposal using the administrator's **Patvirtinti** button.
5. Check the configured Discord task channel and the requester's Telegram chat.
6. Run `/tasks all` again.

Expected: before approval, the proposal is pending and does not appear as a
task. Approval creates one open task, confirms it in Telegram, notifies the
requester, and posts the title, details, deadline, requester, and a mention of
Alex in Discord. The task appears in `/tasks all`.

### 6. Rejected proposal and approval authorization

1. Submit a second clearly labeled UAT task and have the administrator reject
   it with **Atmesti**.
2. Submit another proposal. Have a non-administrator attempt to use its
   approval button, then have the administrator approve it.

Expected: the rejected proposal is not added to the task list and the
requester is notified. A non-administrator cannot approve the second proposal;
the proposal remains available for the administrator, who can approve it.

### 7. Voice task request

1. Send a Telegram voice message requesting a test task. Speak the assignee,
   task, and deadline clearly.
2. Review the administrator's proposal and approve it.

Expected: the bot extracts the request into a proposal, without creating a
task before approval. The approved task is listed and posted to Discord as in
scenario 5.

### 8. Natural-language task questions

1. With the tasks from the previous scenarios present, ask Telegram:
   `Which tasks are due by Friday?`
2. Ask: `What is Alex working on now?`
3. Send a voice message asking a similar question.

Expected: Telegram returns a concise answer based on matching task records;
the answers should reflect the current assignee, status, and due date. These
queries do not create or change tasks.

### 9. Discord task commands and completion permissions

1. As a linked Discord member, run `/tasks` with scope `all`, then with scope
   `mine`.
2. Run `/done` with the ID of an open task assigned to that member.
3. As a different non-administrator member, try `/done` on a task assigned to
   Alex.
4. As the administrator, try `/done` on that same still-open task.

Expected: `/tasks` lists the requested scope. The assignee and administrator
can complete a task; another member cannot. A completed task shows status
`done` in subsequent listings.

### 10. Telegram task commands and reminders

1. From a linked Telegram account, run `/tasks mine` and `/tasks all`.
2. Run `/done ID` for an open task assigned to that account.
3. Leave an open test task with a deadline within the next 24 hours. Call the
   reminder endpoint with the configured secret:

   ```bash
   curl -i -X POST https://YOUR-SERVICE/internal/tick \
     -H "X-Internal-Secret: $INTERNAL_SECRET"
   ```

4. Call the endpoint again and inspect the assignee's Telegram chat.
5. Call the endpoint without the header or with an incorrect secret.

Expected: Telegram lists and completion follow the same permission rules as
Discord. The valid tick returns HTTP 200 and sends a due-soon reminder to the
assigned member; a repeated tick does not send the same reminder again. An
invalid or missing secret returns HTTP 401. The endpoint may also report
`discord_sent` for any assignment notifications still pending delivery.

## Record results

Record each scenario as **Pass**, **Fail**, or **Blocked**, with the deployment
URL (not secrets), UTC/local test time, task IDs, and a short description of
any unexpected result. Do not include bot tokens, API keys, webhook secrets,
or real user IDs in test reports.
