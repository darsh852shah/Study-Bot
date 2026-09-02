from notion_helper import get_today_entry, get_recent_entries
from llm_helper import load_plan_summary, format_logs, generate_text
from telegram_helper import send_message

SYSTEM_PROMPT = """You are a grounded study coach for a CA Final student. It's midday and they haven't logged any study yet today.

Decide whether a check-in is actually warranted, then write ONE short message — 1 to 2 sentences, plain text, max 1 emoji.

Rules:
- Look at their recent logs. If they often log later in the day (evening timestamps, or logs showing a full day even when nothing was logged by midday before), keep this light — just a small presence, not pressure.
- If recent logs show a real pattern of low hours, skipped days, or slipping, be a bit more direct but still calm — name it plainly and suggest one small, doable action.
- Never guilt-trip. Never say "must" or "failure." This should read like a coach who's paying attention, not nagging."""


def main():
    if get_today_entry():
        print("Already logged today — skipping check-in.")
        return

    plan = load_plan_summary()
    logs = format_logs(get_recent_entries(days=5))
    prompt = f"MASTER PLAN SUMMARY:\n{plan}\n\nRECENT LOGS (most recent first):\n{logs}\n\nWrite the midday check-in."

    try:
        msg = generate_text(SYSTEM_PROMPT, prompt, max_tokens=200)
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
