# Study-Bot Webhook Upgrade — Setup Guide

This adds two things on top of your original bot:

1. **Instant replies via webhook** instead of the 15-min GitHub Actions poll.
2. **Conversational Q&A / re-planning** — ask it things like *"how's my AFM going"*,
   *"replan this week"*, *"am I behind on FR"* — it answers using your live Master Plan,
   Daily Log, and Lecture Tracker data, the same way this chat did when it reviewed your Notion.

Your morning nudge, midday check-in, and evening report (`nudge.yml`, `checkin.yml`,
`report.yml`) are untouched and keep running exactly as before on GitHub Actions' cron.
Only the log-polling piece (`poll.yml`) is replaced.

---

## Why this needs a real server (and GitHub Actions can't do it)

GitHub Actions only runs on a schedule or when you manually trigger it — it has no way to
sit and listen for Telegram to push a message at it. A webhook needs a small process that's
always on, listening on a URL. `app.py` is a plain Flask app that works unchanged on Render,
Railway, or Fly.io. These steps use **Render's free tier**, since it needs zero config beyond
env vars.

**Trade-off worth knowing:** Render's free tier spins the service down after ~15 minutes of
no traffic, and takes 30–60s to wake back up on the next request. In practice this means the
*first* message after a quiet stretch might take a bit to get a reply, then it's instant again.
If that's annoying, a paid Render instance (~$7/mo) or Fly.io's free always-on tier avoids it.

---

## 1. Get a Lecture Tracker database ID (new — for the Q&A feature)

The bot already has access to your Daily Log DB and Master Plan page. For lecture-completion
stats, it needs one more ID:

1. Open your **Lecture Tracker** database in Notion (full-page view, not the linked view
   inside the Master Plan).
2. Copy the ID from the URL: `notion.so/yourworkspace/<THIS-PART>?v=...` (32-character string,
   with or without dashes — either works).
3. Make sure your existing Notion integration has access to this database too: `···` menu on
   the database → **Connections** → add your integration if it isn't already there.

## 2. Push this code to your GitHub repo

Copy `app.py`, `query_handler.py`, and the updated `notion_helper.py`, `llm_helper.py`,
`poll_log.py`, and `requirements.txt` into your `Study-Bot` repo, replacing the old versions.
Then:

```bash
git add .
git commit -m "Add webhook + conversational Q&A"
git push
```

(`poll.yml` has been renamed to `poll.yml.disabled` in this bundle — GitHub Actions ignores
`.disabled` files, so it's kept for reference but won't run and won't conflict with the
webhook. You can delete it entirely once you've confirmed the webhook works.)

## 3. Deploy `app.py` to Render

1. Go to [render.com](https://render.com) → **New** → **Web Service** → connect your
   `Study-Bot` GitHub repo.
2. Runtime: **Python 3**. Build command: `pip install -r requirements.txt`. Start command:
   `gunicorn app:app`.
3. Under **Environment**, add these variables (same names as your existing GitHub Actions
   secrets — copy the values over):
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `NOTION_API_KEY`
   - `NOTION_DATABASE_ID`
   - `NOTION_PAGE_ID`
   - `NOTION_LECTURE_DB_ID` ← new, from step 1
   - `GROQ_API_KEY`
   - `TELEGRAM_WEBHOOK_SECRET` ← required, make up any random string (e.g. generate one with
     `python3 -c "import secrets; print(secrets.token_hex(24))"`) — this stops randoms who
     find your Render URL from sending fake messages to your bot.
   - Optional model overrides: `GROQ_CLASSIFIER_MODEL`, `GROQ_ANSWER_MODEL`,
     `GROQ_MEMORY_MODEL`, and `GROQ_LOG_EXTRACTION_MODEL`. Copy the exact API model IDs
     from your Groq console if you need to override the built-in defaults.
4. Deploy. Once it's live, note your service URL, e.g. `https://study-bot-xyz.onrender.com`.

## 4. Point Telegram at your webhook

Run this once (replace the bracketed values):

```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://<your-render-url>/telegram-webhook",
    "secret_token": "<TELEGRAM_WEBHOOK_SECRET>"
  }'
```

A `{"ok":true,"result":true,...}` response confirms it. From this point on, Telegram pushes
every message to your Render app instantly instead of you (or a cron job) polling for it.

**Important:** Telegram only allows *either* long-polling (`getUpdates`, what `poll_log.py`
used) *or* a webhook at a time — not both. Once `setWebhook` succeeds, any code still calling
`getUpdates` will get a `409 Conflict`. This is exactly why `poll.yml` needed to be disabled.

## 5. Test it

- Send `/log AFM:1|3|3|Test|Sleepiness|Sleep earlier` → should get an instant confirmation
  (no more waiting for the next poll cycle).
- Send something like *"How am I doing on FR?"* or *"Replan my week, I'm behind on SPOM"* →
  should get a grounded answer referencing your actual plan/logs/lecture stats, not generic
  advice.
- Send a voice note describing your day → should transcribe and preview once, not twice.

If nothing happens, check the Render service logs — most issues are a missing/misnamed env
var or the Lecture Tracker integration connection from step 1.

## 6. Rolling back

If you ever want to go back to polling: delete the webhook (
`curl "https://api.telegram.org/bot<TOKEN>/deleteWebhook"`), rename
`poll.yml.disabled` back to `poll.yml`, and re-enable that GitHub Actions workflow.

---

## What's different from before, functionally

| | Before | After |
|---|---|---|
| Log a day | Up to 15 min delay (poll cycle) | Instant |
| Ask about your plan/progress | Not possible | Ask anything, anytime — grounded in live plan + logs + lecture tracker |
| Re-planning | Manual (ask in this chat) | Bot can suggest a re-plan on request, using real data |
| State storage | `pending_log.json` / `last_update_id.txt`, committed to the repo by CI | `webhook_state.json` on the server (persists drafts and recently processed Telegram updates across restarts; use shared durable storage before running multiple instances) |
| Morning nudge / midday check-in / evening report | GitHub Actions cron | Unchanged, still GitHub Actions cron |
