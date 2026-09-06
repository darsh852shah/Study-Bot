"""Turns the bot from 'log + scheduled nudges' into something you can actually ask questions to.

answer_query() pulls your live Master Plan, recent Daily Log entries, Lecture Tracker
stats, and long-term memories from Notion, then asks the LLM to answer/re-plan using
ONLY that real data — the same way Claude does when you ask it to check your Notion
setup, just running inside your own bot.
"""

from notion_helper import (
    get_recent_entries, get_lecture_stats, get_memories,
    format_memories, save_memory, today_ist, get_recent_chat_turns,
)
from llm_helper import load_plan_summary, format_logs, format_lecture_stats, generate_text

QUERY_SYSTEM_PROMPT = (
    "You are a direct, grounded study assistant for a CA Final student, scoped ONLY "
    "to their study plan, progress, and how to improve it. Below is the current "
    "date/time, their live master plan, recent daily logs, and lecture tracker stats "
    "— this is the ONLY data you know about their prep. Never invent numbers, "
    "deadlines, lecture counts, or plan phases not given to you; say so plainly if "
    "something's missing. Use the current time (not just date) when relevant — how "
    "much of today is left, whether it's late to still study, proximity to a "
    "scheduled block.\n\n"

    "You also have LONG-TERM MEMORIES from past conversations — preferences, "
    "struggles, patterns, goals. Use these naturally; don't list them back.\n\n"

    "The lecture tracker gives a \"Not started, in syllabus order\" CHAPTER list and, "
    "separately, a \"Next specific lecture to watch\" line — a chapter is not a "
    "lecture. When asked what's next, always use the \"Next specific lecture to "
    "watch\" line verbatim if present. Never substitute a chapter name for a lecture, "
    "guess a lecture/class number not given to you, or reorder/invent "
    "chapters/lectures.\n\n"

    "Default to short: 2-5 sentences or a brief bullet list. Only go longer for an "
    "explicit full re-plan, detailed breakdown, or multiple distinct topics — and "
    "even then, cover only what was asked, no unrequested advice or pep talk unless "
    "the data clearly warrants a specific warning (e.g. very late, today "
    "unlogged).\n\n"

    "You can: answer questions about the plan/phases/deadlines; analyze recent logs "
    "for real patterns (hours vs target, mood/energy trend, recurring distractions) "
    "and name them plainly, without guilt-tripping; suggest a concrete re-plan "
    "grounded in the actual current phase and deadlines when asked or clearly "
    "warranted; report lecture-completion status per subject with realistic pacing "
    "math (lectures left vs days to deadline). For a simple greeting, just greet "
    "back — no data summary or unsolicited advice.\n\n"

    "You do NOT edit the master plan — only advise. You cannot save anything: no "
    "logging hours, marking lectures Watched, or writing to Notion. NEVER say "
    "something has been \"logged,\" \"saved,\" or \"marked Watched\" by you — if the "
    "student describes finishing a lecture/session, tell them to send it as a real "
    "log (voice note, text, or `/log ...`) instead of pretending it's already "
    "saved.\n\n"

    "This is a Telegram chat — format with bullet points/short paragraphs/bold for "
    "emphasis, reasonably concise. Reference specific numbers from the data so it's "
    "clearly not generic (unless just greeting)."
)
MEMORY_EXTRACT_PROMPT = """You are analyzing a study-assistant conversation for a CA Final student. Your job is to extract any NEW long-term facts worth remembering for future conversations.

Only extract genuinely useful, specific facts — things like:
- Study preferences ("prefers studying at night", "likes 1.5x lecture speed")
- Recurring struggles ("consistently low energy on Mondays", "AFM theory chapters are hard")
- Goals or commitments ("wants to finish FR by October", "aims for 6h/day minimum")
- Patterns ("mood drops after 0-hour days", "skips morning blocks often")
- Personal context ("has back pain issues", "ITT classes on weekdays")

Do NOT extract:
- Generic facts obvious from the plan itself
- Anything already in the existing memories
- Trivial single-conversation details

Output a JSON array of objects. Each object has:
{"memory": "short factual sentence", "category": "preference|pattern|struggle|goal|insight"}

If nothing new is worth remembering, output an empty array: []

Output ONLY valid JSON — no markdown, no commentary."""


def build_context(history_days=14):
    """Assembles Master Plan, Daily Log, Lecture Tracker, and Long-term Memories
    into one text block for the LLM prompt."""
    plan = load_plan_summary() or "Master Plan unavailable right now."
    logs = format_logs(get_recent_entries(days=history_days))
    lecture_text = format_lecture_stats(get_lecture_stats())
    memory_text = format_memories(get_memories())

    now = today_ist()
    today_str = now.strftime("%A, %d %B %Y")
    time_str = now.strftime("%I:%M %p").lstrip("0") + " IST"

    return (
        f"TODAY'S DATE: {today_str}\n"
        f"CURRENT TIME: {time_str}\n\n"
        f"MASTER PLAN:\n{plan}\n\n"
        f"LECTURE TRACKER (FR / AFM):\n{lecture_text}\n\n"
        f"RECENT DAILY LOGS (most recent first, last {history_days} days):\n{logs}\n\n"
        f"LONG-TERM MEMORIES (things learned about this student from past conversations):\n{memory_text}"
    )


def answer_query(user_message):
    """Fetches recent chat turns from Notion's Chat Log (last 90 minutes) to give the LLM a
    little short-term memory, so back-to-back questions feel like a conversation rather than
    resetting every time. Pulled from Notion rather than passed in by the caller so this
    memory survives Render free-tier cold starts, which wipe any in-memory state app.py holds."""
    chat_history = get_recent_chat_turns(minutes=90)
    context = build_context()

    convo = ""
    if chat_history:
        transcript = "\n".join(
            f"{'You (assistant)' if role == 'assistant' else 'Student'}: {msg}"
            for role, msg in chat_history
        )
        convo = f"\n\nRECENT CONVERSATION (for continuity, oldest first):\n{transcript}"

    user_prompt = (
        f"{context}{convo}\n\n"
        f'Student\'s message just now: "{user_message}"\n\n'
        "Respond as their study assistant."
    )
    reply = generate_text(QUERY_SYSTEM_PROMPT, user_prompt, max_tokens=4096)

    # Extract and save any new long-term memories from this exchange. Best-effort — a failure
    # here should never affect the user's reply. (Previously this only ran every 3rd query via
    # an in-memory counter, but that counter resets on every process restart — and Render's
    # free tier spins the service down when idle — so it rarely survived long enough to reach
    # 3 in practice. Running it every time costs one extra fast Groq call, which is cheap.)
    try:
        _extract_and_save_memories(user_message, reply, get_memories())
    except Exception as e:
        print(f"Memory extraction failed: {e}")

    return reply


def _extract_and_save_memories(user_message, bot_reply, existing_memories):
    """Asks the LLM to extract any new long-term facts from this exchange, then saves them."""
    import json

    existing_text = format_memories(existing_memories) if existing_memories else "None yet."
    user_prompt = (
        f"EXISTING MEMORIES (do not duplicate these):\n{existing_text}\n\n"
        f"STUDENT'S MESSAGE:\n{user_message}\n\n"
        f"ASSISTANT'S REPLY:\n{bot_reply}\n\n"
        "Extract new memories as a JSON array."
    )
    raw = generate_text(MEMORY_EXTRACT_PROMPT, user_prompt, max_tokens=1024, reasoning_effort="none")

    # Parse the JSON array
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return
    memories = json.loads(text[start:end + 1])
    if not isinstance(memories, list):
        return

    valid_categories = {"preference", "pattern", "struggle", "goal", "insight"}
    for mem in memories:
        if isinstance(mem, dict) and mem.get("memory"):
            category = mem.get("category", "insight")
            if category not in valid_categories:
                category = "insight"
            save_memory(mem["memory"], category=category, source="bot-inferred")
