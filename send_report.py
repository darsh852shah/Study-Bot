import logging

from notion_helper import get_today_entry, get_recent_entries
from llm_helper import COACH_MODEL, load_plan_summary, format_logs, generate_text, trim_prompt_text
from telegram_helper import send_message

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a direct, grounded study coach for a CA Final student preparing for the May 2027 exam.
You're given their master study plan and their last few days of logged study data, including today's.

Write a SHORT end-of-day reflection — 1 to 2 sentences, plain text, max 1 emoji.

Rules:
- This is NOT the stats readout (that's shown separately) — this is a brief coaching observation.
- If today or the recent trend is weak (low hours, low mood/energy, recurring 'broke focus' reasons), name the pattern plainly and suggest ONE small adjustment for tomorrow — not a lecture, not guilt.
- If today or the trend is solid, say specifically what's working, not generic praise.
- Never say "must" or "failure." Keep it calm and useful, like a coach who's paying attention, not a hype machine."""


def plain_text(rich_text_list):
    return "".join(t.get("plain_text", "") for t in rich_text_list)


def main():
    entry = get_today_entry()

    if not entry:
        send_message(
            "No log found for today yet.\n\n"
            "Log it now:\n`/log activity:hrs,activity:hrs|mood|energy|win|broke|fix`\n\n"
            "_Example:_ `/log AFM:1,ITT:6|3|2|Watched 1 AFM lecture, sat ITT|Sleepiness|Sleep earlier`"
        )
        return

    props = entry["properties"]
    hours = props["Time effective (hrs)"]["number"]
    mood = props["Mood (1–5)"]["number"]
    energy = props["Energy (1–5)"]["number"]
    win = plain_text(props["Win"]["rich_text"])
    broke = ", ".join(t["name"] for t in props["What broke focus"]["multi_select"])
    fix = plain_text(props["Fix for tomorrow"]["rich_text"])
    breakdown = plain_text(props.get("Activity Breakdown", {}).get("rich_text", []))

    stats_msg = (
        f"📊 *Today's report*\n\n"
        f"Effective hours: {hours if hours is not None else '—'}\n"
        f"Breakdown: {breakdown or '—'}\n"
        f"Mood: {mood if mood is not None else '—'}/5  |  Energy: {energy if energy is not None else '—'}/5\n"
        f"Win: {win or '—'}\n"
        f"Broke focus: {broke or '—'}\n"
        f"Fix for tomorrow: {fix or '—'}"
    )
    send_message(stats_msg)

    try:
        plan = load_plan_summary()
        logs = format_logs(get_recent_entries(days=5))
        user_prompt = (
            f"MASTER PLAN SUMMARY:\n{trim_prompt_text(plan, 8000)}\n\n"
            f"RECENT LOGS (most recent first, includes today):\n{trim_prompt_text(logs, 4000)}\n\n"
            "Write tonight's coaching reflection."
        )
        coach_msg = generate_text(
            SYSTEM_PROMPT, user_prompt, model=COACH_MODEL,
            max_tokens=280, reasoning_effort="low",
        )
        send_message(coach_msg)
    except Exception:
        logger.exception("Evening coaching reflection failed after stats were sent")


if __name__ == "__main__":
    main()
