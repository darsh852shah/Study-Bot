from datetime import datetime, timezone, timedelta

from notion_helper import get_recent_entries
from llm_helper import CLASSIFIER_MODEL, load_plan_summary, format_logs, generate_text, trim_prompt_text
from telegram_helper import send_message

IST = timezone(timedelta(hours=5, minutes=30))

SYSTEM_PROMPT = """You are a direct, grounded study coach for a CA Final student preparing for the May 2027 exam.
You're given their master study plan and their last few days of logged study data.

Write a SHORT morning nudge — 2 to 4 sentences, plain text, max 1 emoji. Do NOT include a greeting like "Good morning" or the date — that's added separately.

Rules:
- Be specific. Reference something real from the plan or recent logs — a subject, a deadline, an actual number from recent days. Never generic motivational-poster language ("seize the day", "you've got this").
- If recent logs show low hours, low mood/energy, or a gap versus what's needed, acknowledge it in one clause without dwelling on it, then give exactly ONE small, concrete next action for today — name the subject/topic, not just "start studying."
- If recent logs show good momentum, name specifically what's working (e.g. consistent hours on a subject, a mood/energy trend) and encourage keeping that same shape of day — don't inflate it with over-the-top praise.
- If there's no recent log data, reference the current phase of the plan and give a clear, calm nudge to start the first block of that.
- Never guilt-trip. Never use words like "must," "failure," or "should have." Keep it steady, warm, and a little human — like someone who's actually been paying attention, not a template."""


def greeting():
    now = datetime.now(IST)
    day_str = now.strftime("%A, %d %b")
    return f"☀️ Good morning — {day_str}"


def main():
    plan = load_plan_summary()
    logs = format_logs(get_recent_entries(days=5))
    user_prompt = (
        f"MASTER PLAN SUMMARY:\n{trim_prompt_text(plan, 8000)}\n\n"
        f"RECENT LOGS (most recent first):\n{trim_prompt_text(logs, 4000)}\n\n"
        "Write today's morning nudge."
    )

    try:
        body = generate_text(
            SYSTEM_PROMPT, user_prompt, model=CLASSIFIER_MODEL,
            max_tokens=180, reasoning_effort="none",
        )
    except Exception as e:
        print(f"generate_text failed, using fallback nudge: {type(e).__name__}: {e}")
        body = (
            "The plan's still live and today's block is waiting on you — "
            "start with whatever's next in the current phase before anything else gets a look-in."
        )

    msg = (
        f"{greeting()}\n\n"
        f"{body}\n\n"
        "Log today anytime — voice note, plain text, or:\n"
        "`/log activity:hrs,activity:hrs|mood|energy|win|broke|fix`"
    )
    send_message(msg)


if __name__ == "__main__":
    main()
