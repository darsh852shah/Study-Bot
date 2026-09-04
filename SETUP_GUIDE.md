# CA Study Bot — Setup Guide

Free stack: **Telegram Bot** (messaging) + **GitHub Actions** (scheduling, free tier) + **Notion API** (writes to your existing "Daily Log (DB)") + **Groq** (free-tier hosted LLM — Qwen3-32B for coaching/extraction — and Whisper for voice transcription).

What it does:
- **7:00 AM IST** — an LLM-generated nudge, written using your master plan + your last 5 days of logs, so it actually reacts to how you've been doing (not a canned message)
- **Anytime** — you log your day by **voice note, a normal sentence, or the old `/log ...` shortcut** — the bot writes a row into your Notion Daily Log
- **1:00 PM IST** — a midday check-in, but only if you haven't logged anything yet
- **9:30 PM IST** — sends your raw stats for the day, plus a short LLM coaching line — if the trend's weak it names it gently and gives one concrete fix; if it's strong it tells you what's working

**Important honesty note:** this isn't a fully autonomous "agent" that takes actions on its own — it reads your plan/logs and generates messages + writes log entries. That's what you asked for, and it's the right amount of automation for this use case. It won't message you unprompted outside its fixed check-in points, it never edits your plan itself, and it never saves a log without you confirming it first.

---

## Step 1 — Create the Telegram bot

1. Open Telegram, search for **@BotFather**, start a chat
2. Send `/newbot`, give it a name and a username (must end in `bot`, e.g. `darsh_ca_study_bot`)
3. BotFather replies with a **token** like `123456789:AAExxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` — save this, it's your `TELEGRAM_BOT_TOKEN`

## Step 2 — Get your chat ID

1. Search for your new bot in Telegram and send it any message (e.g. "hi")
2. In a browser, open:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
   (replace `<YOUR_TOKEN>` with the token from Step 1)
3. Find `"chat":{"id":123456789,...}` in the response — that number is your `TELEGRAM_CHAT_ID`

## Step 3 — Create a Notion integration

1. Go to **notion.so/my-integrations** → **New integration**
2. Name it anything (e.g. "Study Bot"), select your workspace, create it
3. Copy the **Internal Integration Token** — this is your `NOTION_API_KEY`

## Step 4 — Connect the integration to your Daily Log database

1. Open your **CA Final — May 2027 Master Plan** page in Notion, scroll to the **Daily Log (DB)**
2. Open the database as a full page (click the title / expand icon)
3. Click the **`•••`** menu (top right) → **Connections** → **Add connection** → select your "Study Bot" integration
4. Copy the **database URL** — the 32-character ID right after your workspace name and before any `?`, e.g.:
   `notion.so/yourworkspace/`**`d7079de1b9d74bd483d40e510a1d8b50`**`?v=...`
   That string is your `NOTION_DATABASE_ID`

## Step 5 — Create a free Groq account (for the LLM and voice transcription)

1. Go to **console.groq.com** → sign up (no credit card needed)
2. Go to **API Keys** → **Create API Key** → copy it — this is your `GROQ_API_KEY`
3. This one key covers everything: the coaching/nudge text, extracting structured data from your free-text or voice logs, and transcribing voice notes. Groq's free tier has generous headroom for the handful of calls this bot makes per day — a nudge, a report, and however many times you log.

## Step 6 — Get your Notion page ID (for live plan sync)

1. Open your **CA Final — May 2027 Master Plan** page in Notion
2. Copy the page URL — the 32-character string right after your workspace name is the page ID, e.g.:
   `notion.so/yourworkspace/`**`3b23c85633ed81158c84e7bc88fb632b`**
   That's your `NOTION_PAGE_ID` — this lets the bot read your actual plan live instead of a static copy

## Step 7 — Create a GitHub repo

1. Go to **github.com** → **New repository** (can be private)
2. Upload all the files from this folder, keeping the folder structure exactly as-is:
   - `notion_helper.py`
   - `telegram_helper.py`
   - `llm_helper.py`
   - `stt_helper.py`
   - `plan_summary.txt`
   - `send_nudge.py`
   - `send_report.py`
   - `check_in.py`
   - `poll_log.py`
   - `requirements.txt`
   - `last_update_id.txt`
   - `pending_log.json`
   - the `.github/workflows/` folder with its 4 files

## Step 8 — Add your secrets

In your new repo: **Settings** → **Secrets and variables** → **Actions** → **New repository secret**. Add these six, one at a time — **no new secrets are needed for voice/free-text logging**, it all runs on the same `GROQ_API_KEY`:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | from Step 1 |
| `TELEGRAM_CHAT_ID` | from Step 2 |
| `NOTION_API_KEY` | from Step 3 |
| `NOTION_DATABASE_ID` | from Step 4 |
| `GROQ_API_KEY` | from Step 5 |
| `NOTION_PAGE_ID` | from Step 6 |

## Step 9 — Enable and test

1. Go to the **Actions** tab in your repo — click "I understand my workflows, enable them" if prompted
2. Click **Morning Nudge** → **Run workflow** → **Run workflow** (manual trigger) — you should get a Telegram message within ~30 seconds
3. Same for **Evening Report** — since nothing's logged yet, it'll ask you to log
4. Test logging three ways:
   - **Voice note:** record yourself saying something like *"Did AFM for one hour and ITT for six, mood was okay, energy was low, win was finishing FR chapter 5, kept getting pulled onto my phone, going to keep it in another room tomorrow"* and send it to the bot
   - **Free text:** send the same thing as a typed sentence
   - **Strict shortcut:** send `/log AFM:1,ITT:6|3|2|Finished FR ch5|Phone / scrolling|Phone in another room`
5. For voice/free text, the bot replies with what it understood — reply **yes** to save it, or send a correction (e.g. "actually energy was 3") and it'll re-check with you. The `/log` shortcut saves immediately with no confirmation step.
6. Manually run **Poll Telegram Logs** after sending any of the above — check your Notion Daily Log DB, a new row should appear
7. Run **Evening Report** again — it should now show today's real numbers

Once confirmed working, the schedules run automatically — no need to trigger anything manually again.

---

## How to log day-to-day

You have three ways to log, from most to least effortful:

### 1. Voice note (easiest)
Just talk. Send the bot a voice message describing your day — activities, hours, mood, energy, what went well, what broke your focus, and your plan for tomorrow, in whatever order feels natural. The bot transcribes it, pulls out the structured fields, and shows you what it understood before saving anything.

### 2. Free text
Same as above, just typed instead of spoken: *"Did AFM 1h and ITT 6h, mood 3, energy 2, finished FR chapter 5, kept getting distracted by my phone, plan is to keep it in another room tomorrow."* No fixed format required — write it however you'd naturally say it.

### 3. The old strict shortcut (fastest, no confirmation step)
```
/log activity:hrs,activity:hrs|mood|energy|win|broke|fix
```
**Example (mixed day):**
```
/log AFM:1,ITT:6|3|2|Watched 1 AFM lecture, sat ITT|Sleepiness|Sleep earlier
```
This saves immediately — no confirmation, since the format is already unambiguous. Good for days you want to log fast without reading anything back.

### The confirmation step (voice & free text only)
After a voice note or free-text message, the bot replies with something like:

> Got it — here's what I understood:
> • Total: 7h (AFM: 1h, ITT: 6h)
> • Mood: 3/5 | Energy: 2/5
> • Win: Finished FR ch5
> • Broke focus: Phone / scrolling
> • Fix for tomorrow: Phone in another room
>
> Reply *yes* to save, or just tell me what to fix.

- Reply **yes** (or "yep", "confirm", "save", etc.) → it saves to Notion
- Reply **no** / **cancel** → it discards the draft, no harm done
- Reply with anything else → it's treated as a correction or addition (e.g. "energy was actually 4, not 2") and it re-extracts, shows you the updated version, and waits again
- If it couldn't figure out your hours, mood, or energy at all, it'll say so and ask specifically for what's missing rather than guessing

### Logging in pieces across the day
Since anything you send while a draft is pending gets merged into it rather than starting a new entry, you can genuinely build one day's log up over time — activities in the morning, mood and energy in the evening, whatever order suits you. Just don't say "yes" until it's actually complete (or until you're happy saving it as-is).

### Starting a fresh log without confirming/cancelling first
If you want to abandon whatever's pending and start over — say you began a log this morning and just want to scrap it and log a completely different day — send **"new log"**, **"new entry"**, **"start over"**, **"start fresh"**, or **"reset"**. This immediately clears the pending draft. You can also put your new log right after it in the same message, e.g. *"new log: did SPOM for 3 hours, mood 4"*, and it'll extract and confirm the new one right away instead of asking you to send it separately.

Across all three methods:
- Activity names fuzzy-match against: SPOM, ITT, GMCS, FR, AFM, DT, IDT, Audit, IBS, Revision, Mock, Other
- Mood and energy are 1–5
- "Broke focus" fuzzy-matches against: Phone / scrolling, Hunger / low energy, Noise / people, Anxiety / overthinking, Hard topic / confusion, Sleepiness, Planning too long, Other
- Total effective hours, the Activity tags, and the readable breakdown are all computed automatically — no need to add anything up yourself

Everything is checked and saved within 15 minutes, during the 9:30 AM – 9:30 PM polling window.

---

## Notes & limits

- **Fully free** — GitHub Actions free tier gives 2,000 min/month on private repos (this uses a few minutes/day); Telegram, Notion, and Groq's free tiers cover everything else
- **Poll runs every 15 min, only 9:30 AM–9:30 PM IST** — logging outside that window will queue and get picked up at the next run
- **The bot only remembers one in-progress draft at a time**, so it can merge partial logs sent across the day. If you want to abandon a stale draft instead of merging into it, say "new log" (see above) rather than just sending an unrelated message.
- **A confirmation only lasts until you resolve it** — reply yes/no/correction whenever you're free; there's no timeout, it just sits pending until the next poll picks up your reply
- **All times are UTC in the cron schedules** — already converted to IST above; if you ever change them, remember GitHub Actions cron uses UTC
- If a workflow ever silently stops firing, GitHub sometimes disables scheduled workflows on repos with no activity for 60+ days — just re-enable from the Actions tab

## Keeping it accurate

With the `NOTION_PAGE_ID` secret set, the bot reads your **live Master Plan page** on every nudge/report run — no more manually syncing `plan_summary.txt`. The static file is kept as a fallback only, used if the live fetch ever fails (rate limit, connection issue). You can leave it as-is or delete its content; it won't go stale in a way that matters anymore.

## What this bot deliberately does NOT do

- **It never saves a voice or free-text log without you confirming it.** LLM extraction from casual speech won't always be perfect — a misheard number or a skipped activity is possible — so nothing hits Notion until you've seen the parsed version and said yes.
- **It never edits your Notion plan.** It reads it, reacts to it, and writes to the Daily Log — but it won't rewrite deadlines or phases on its own. An LLM occasionally gets details wrong, and a plan quietly drifting without your review would defeat the point of having one.
- **It only initiates contact at 3 fixed points** (7 AM nudge, 1 PM check-in only if you haven't logged, 9:30 PM report) — not a continuously "thinking" process. That's a deliberate limit: more trigger points mean more chances for a bad decision to reach you unfiltered, and three well-placed touches a day is enough for this to actually help rather than become noise.

## If the LLM or transcription output ever feels off

- Groq occasionally rate-limits or times out — `send_nudge.py`/`send_report.py` fall back to a plain message without breaking if that happens; for logging, a failed extraction just tells you to try again or use `/log`
- To switch the coaching/extraction model from Qwen to Gemma, open `llm_helper.py` and change `MODEL = "qwen/qwen3-32b"` to `MODEL = "gemma2-9b-it"` (note: Gemma can't do the JSON extraction as reliably as Qwen — if free-text/voice parsing gets noticeably worse after switching, switch back)
- To change the transcription model, open `stt_helper.py` and change `STT_MODEL` — `whisper-large-v3-turbo` (default) is fast and multilingual; `distil-whisper-large-v3-en` is a bit faster and English-only
- If messages feel too soft or too harsh, adjust the `SYSTEM_PROMPT` text at the top of `send_nudge.py` / `send_report.py` / `check_in.py` — that's the place controlling tone
- If free-text/voice extraction keeps misreading your logs the same way, adjust `EXTRACT_SYSTEM_PROMPT` at the top of `llm_helper.py`
