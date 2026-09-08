from notion_helper import get_today_entry, get_recent_entries, get_lecture_stats
from llm_helper import COACH_MODEL, load_plan_summary, format_logs, format_overall_lecture_pct, generate_text, trim_prompt_text
from telegram_helper import send_message

lecture_text = format_overall_lecture_pct(get_lecture_stats())

SYSTEM_PROMPT = """You are a grounded study coach for a CA Final student. It's midday and they haven't logged any study yet today.
Decide whether a check-in is actually warranted, then write ONE short message — 1 to 2 sentences, plain text, max 1 emoji.
Rules:
- NEVER mention any subject name, topic name, chapter name, or lecture name — not the plan's, not from recent logs. This is a hard rule, no exceptions.
- You may reference the overall lecture-completion percentage if it's given to you — that figure never refers to a single subject or lecture.
- Talk only in terms of hours, streaks, timing patterns, mood/energy trend, and optionally the one overall lecture-completion percentage.
- Look at their recent logs. If they often log later in the day (evening timestamps, or logs showing a full day even when nothing was logged by midday before), keep this light — just a small presence, not pressure.
- If recent logs show a real pattern of low hours, skipped days, or slipping, be a bit more direct but still calm — name it plainly (in terms of hours/consistency, not subject matter) and suggest one small, generic, doable action (e.g. start with just 30 minutes now, do one focused block before anything else).
- Never guilt-trip. Never say "must" or "failure." This should read like a coach who's paying attention, not nagging."""


def main():
    if get_today_entry():
        print("Already logged today — skipping check-in.")
        return
    plan = load_plan_summary()
    logs = format_logs(get_recent_entries(days=5))
    prompt = (
        f"MASTER PLAN SUMMARY:\n{trim_prompt_text(plan, 8000)}\n\n"
        f"LECTURE TRACKER:\n{trim_prompt_text(lecture_text, 2000)}\n\n"
        f"RECENT LOGS (most recent first):\n{trim_prompt_text(logs, 4000)}\n\n"
        "Write the midday check-in. Remember: no subject, topic, chapter, or lecture names — "
        "use only hours/streaks/timing/mood-energy language."
    )
    try:
        msg = generate_text(
            SYSTEM_PROMPT, prompt, model=COACH_MODEL,
            max_tokens=280, reasoning_effort="low",
        )
    except Exception as e:
        # Previously failed with zero output — impossible to diagnose from the Actions log.
        # Print the real reason so the next silent skip is actually visible.
        print(f"generate_text failed, skipping check-in: {type(e).__name__}: {e}")
        return
    print(f"Sending check-in: {msg!r}")
    send_message(msg)
    print("Sent.")


if __name__ == "__main__":
    main()
