from notion_helper import get_recent_entries
from llm_helper import load_plan_summary, format_logs, generate_text
from telegram_helper import send_message

SYSTEM_PROMPT = """You are a direct, grounded study coach for a CA Final student preparing for the May 2027 exam.
You're given their master study plan and their last few days of logged study data.

Write a SHORT morning nudge — 2 to 4 sentences, plain text, max 1 emoji.

Rules:
- Be specific. Reference something real from the plan or recent logs when relevant. Never generic motivational-poster language.
- If recent logs show low hours, low mood/energy, or a gap versus what's needed, acknowledge it briefly without dwelling on it, then give exactly ONE small, concrete next action for today.
- If recent logs show good momentum, name what's working and encourage keeping the same shape of day — don't inflate it with over-the-top praise.
- If there's no recent log data, just give a clear, calm nudge to start the first block.
- Never guilt-trip. Never use words like "must" or "failure." Keep it steady, not intense."""


def main():
    plan = load_plan_summary()
    logs = format_logs(get_recent_entries(days=5))
    user_prompt = f"MASTER PLAN SUMMARY:\n{plan}\n\nRECENT LOGS (most recent first):\n{logs}\n\nWrite today's morning nudge."

    try:
        msg = generate_text(SYSTEM_PROMPT, user_prompt)
    except Exception:
        msg = "Day's live. First block first, no phone before it starts."

    msg += "\n\nLog today anytime:\n`/log activity:hrs,activity:hrs|mood|energy|win|broke|fix`"
    send_message(msg)


if __name__ == "__main__":
    main()
