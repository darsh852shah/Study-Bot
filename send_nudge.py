from datetime import datetime, timezone, timedelta
from notion_helper import get_recent_entries, get_lecture_stats
from llm_helper import COACH_MODEL, load_plan_summary, format_logs, format_overall_lecture_pct, generate_text, trim_prompt_text
from telegram_helper import send_message

IST = timezone(timedelta(hours=5, minutes=30))
lecture_text = format_overall_lecture_pct(get_lecture_stats())

SYSTEM_PROMPT = """You are a direct, grounded study coach for a CA Final student preparing for the May 2027 exam.
You're given their master study plan and their last few days of logged study data.
Write a SHORT morning nudge — 2 to 4 sentences with emoji. Do NOT include a greeting like "Good morning" or the date — that's added separately.
Rules:
- NEVER mention any subject name, topic name, chapter name, or lecture name — not the plan's, not from recent logs. This is a hard rule, no exceptions.
- You may reference the overall lecture-completion percentage if it's given to you — that figure never refers to a single subject or lecture.
- Be specific using only numbers and patterns: hours studied, streaks, a mood/energy trend, how today's hours compare to the recent average or the daily target, and optionally the one overall lecture-completion percentage. Give a concrete hours target for today (e.g. "let's target 4 hours today").
- If recent logs show low hours, low mood/energy, or a gap versus what's needed, acknowledge it in one clause without dwelling on it, then pivot to a clear hours-based target for today.
- If recent logs show good momentum, name specifically what's working in terms of hours/consistency/mood-energy trend (not subject matter) and encourage keeping that same shape of day — don't inflate it with over-the-top praise.
- If there's no recent log data, give a clear, calm nudge to just get started today, framed as a time/hours goal — not a specific task or topic.
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
        f"LECTURE TRACKER:\n{trim_prompt_text(lecture_text, 2000)}\n\n"
        f"RECENT LOGS (most recent first):\n{trim_prompt_text(logs, 4000)}\n\n"
        "Write today's morning nudge. Remember: no subject, topic, chapter, or lecture names — "
        "use only hours/streaks/mood-energy numbers, the overall lecture-completion percentage if "
        "relevant, and give a concrete hours target for today."
    )
    try:
        body = generate_text(
            SYSTEM_PROMPT, user_prompt, model=COACH_MODEL,
            max_tokens=350, reasoning_effort="low",
        )
    except Exception as e:
        print(f"generate_text failed, using fallback nudge: {type(e).__name__}: {e}")
        body = (
            "Let's get it done today — aim for a solid 4 hours and keep the streak alive. "
            "One focused block now beats a scattered day later. 💪"
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
