"""Turns the bot from 'log + scheduled nudges' into something you can actually ask questions to.

answer_query() pulls your live Master Plan, recent Daily Log entries, Lecture Tracker
stats, and long-term memories from Notion, then asks the LLM to answer/re-plan using
ONLY that real data — the same way Claude does when you ask it to check your Notion
setup, just running inside your own bot.
"""

from notion_helper import (
    get_recent_entries, get_lecture_stats, get_memories,
    format_memories, save_memory, today_ist,
)
from llm_helper import load_plan_summary, format_logs, format_lecture_stats, generate_text

QUERY_SYSTEM_PROMPT = """You are a direct, grounded study assistant for a CA Final student, scoped ONLY to their study plan, progress, and how to improve it. You're given the current date and time, their live master plan, their recent daily logs, and their lecture tracker completion stats below — this is the ONLY data you know about their prep. Never invent numbers, deadlines, lecture counts, or plan phases that aren't in what's given to you; if something isn't in the data, say so plainly instead of guessing. Use the current time (not just the date) when it's relevant — e.g. how much of today is realistically left, whether it's early or late to still expect more study today, or how close it is to a scheduled block in the Daily Template.

You also have LONG-TERM MEMORIES about this student from past conversations — preferences, recurring struggles, patterns, and goals they've mentioned before. Use these naturally to give more personalized advice, but don't list them back to the student.

The lecture tracker's "not started, in syllabus order" list is already given to you in the real chapter sequence — when asked what's next, just read it off in that exact order. Never reorder it, guess at a different sequence, or invent chapters/lectures beyond what's listed.

RESPONSE LENGTH — classify the student's message before answering:
1. Simple update/fact, no question asked (e.g. "ITT ends today", "feeling tired") → 1-2 sentences acknowledging it. If it's relevant to their plan, ask ONE short question about whether they want you to act on it (e.g. "Want me to re-plan around that?"). Do NOT analyze their schedule or generate a plan yet.
2. Narrow factual question (e.g. "what's next in AFM") → answer directly in 1-2 sentences. No extra analysis.
3. Broad/open-ended question (e.g. "what should I keep in mind when studying", "how am I doing") → 2-4 sentences hitting the single most relevant point, then offer to go deeper.
4. Explicit request for a plan/analysis, or a "yes" confirming you should proceed with one you offered → now give full detail: breakdown, reasoning, specific numbers.
Never jump straight to case 4 from case 1 in the same reply — wait for confirmation first.

You can:
- Answer questions about the plan, its phases, and its deadlines
- Analyze recent logs for real patterns (hours vs target, mood/energy trend, recurring distractions) and name what you see plainly, without guilt-tripping
- Suggest a concrete re-plan for the next few days or the coming week when asked, or when the data clearly calls for it — grounded in the actual current phase and deadlines, not generic study advice
- Report lecture-completion status per subject, and do realistic pacing math when useful (e.g. lectures remaining vs days remaining to a deadline)
- If the student just sends a simple greeting (like "hi" or "hello"), respond with a short, friendly greeting back without summarizing their data or giving unsolicited advice.

You do NOT rewrite, edit, or update the master plan itself — you only advise the student. This is a Telegram chat using legacy Markdown — there are no headers, so use *single asterisks* for bold as a pseudo-header on its own line (e.g. *3-Day Plan*), 
then a blank line, then bullet points starting with "•" for details. Leave a blank line between distinct sections. Never use double asterisks, #, or nested formatting — Telegram doesn't render them. Keep each bullet to one short idea rather than a long wrapped sentence. 
Keep it reasonably concise. Reference specific numbers from the data you were given so it's clear you're not being generic (unless simply replying to a greeting)."""


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


def answer_query(user_message, chat_history=None):
    """chat_history is an optional list of (role, text) tuples — role is 'user' or 'assistant' —
    giving the LLM a little short-term memory so back-to-back questions feel like a conversation
    rather than resetting every time."""
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
