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
from llm_helper import ANSWER_MODEL, MEMORY_MODEL, load_plan_summary, format_logs, format_lecture_stats, generate_text

NORMAL_REPLY_TOKEN_LIMIT = 450
DETAILED_REPLY_TOKEN_LIMIT = 900
NORMAL_REPLY_CHARACTER_LIMIT = 1_200
DETAILED_REPLY_CHARACTER_LIMIT = 2_400

QUERY_SYSTEM_PROMPT = """You are a direct, grounded study assistant for a CA Final student, scoped ONLY to their study plan, progress, and how to improve it. You're given the current date and time, their live master plan, their recent daily logs, and their lecture tracker completion stats below — this is the ONLY data you know about their prep. Never invent numbers, deadlines, lecture counts, or plan phases that aren't in what's given to you; if something isn't in the data, say so plainly instead of guessing. Use the current time (not just the date) when it's relevant — e.g. how much of today is realistically left, whether it's early or late to still expect more study today, or how close it is to a scheduled block in the Daily Template.

You also have LONG-TERM MEMORIES about this student from past conversations — preferences, recurring struggles, patterns, and goals they've mentioned before. Use these naturally to give more personalized advice, but don't list them back to the student.

The lecture tracker's "not started, in syllabus order" list is already given to you in the real chapter sequence — when asked what's next, just read it off in that exact order. Never reorder it, guess at a different sequence, or invent chapters/lectures beyond what's listed.

RESPONSE LENGTH — classify the student's message before answering. Brevity is a hard
requirement, not a suggestion:
1. Simple update/fact, no question asked (e.g. "ITT ends today", "feeling tired") → 1-2 sentences acknowledging it. If it's relevant to their plan, ask ONE short question about whether they want you to act on it (e.g. "Want me to re-plan around that?"). Do NOT analyze their schedule or generate a plan yet.
2. Narrow factual question (e.g. "what's next in AFM") → answer directly in 1-2 sentences. No extra analysis.
3. Broad/open-ended question (e.g. "what should I keep in mind when studying", "how am I doing") → at most 3 short sentences hitting the single most relevant point, then offer to go deeper.
4. Explicit request for a plan/analysis, or a "yes" confirming you should proceed with one you offered → give useful detail, but no more than 8 short bullets and no repeated context.
Never jump straight to case 4 from case 1 in the same reply — wait for confirmation first.
5. Closing/decline/acknowledgment (e.g. "no", "nope", "nope thanks", "bye", "thanks", "that's all", "ok") → reply with ONE short line acknowledging it — no question, no restating what you'll do, no offering alternatives. 
Just close warmly (e.g. "Sounds good — here if you need anything." or "👍 Talk later."). 
Never follow a decline with another question or a summary of your capabilities.

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
  {"memory": "short factual sentence", "category": "preference|pattern|struggle|goal|insight", "source": "user-stated|bot-inferred"}

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
    detailed = _is_detailed_request(user_message)
    reply = generate_text(
        QUERY_SYSTEM_PROMPT,
        user_prompt,
        model=ANSWER_MODEL,
        max_tokens=DETAILED_REPLY_TOKEN_LIMIT if detailed else NORMAL_REPLY_TOKEN_LIMIT,
    )
    reply = _trim_reply(
        reply,
        DETAILED_REPLY_CHARACTER_LIMIT if detailed else NORMAL_REPLY_CHARACTER_LIMIT,
    )

    if _is_memory_worthy(user_message):
        try:
            _extract_and_save_memories(user_message, reply, get_memories())
        except Exception as e:
            print(f"Memory extraction failed: {e}")

    return reply


def _is_detailed_request(user_message):
    """Reserve the larger response budget for an explicitly requested plan or analysis."""
    import re

    text = (user_message or "").lower()
    return bool(re.search(
        r"\b(?:replan|plan|schedule|timetable|detailed|full|deep|analyse|analyze|analysis|"
        r"breakdown|week(?:ly)? plan|catch[ -]?up)\b",
        text,
    ))


def _trim_reply(reply, character_limit):
    """Keep an unexpectedly verbose model response readable in Telegram.

    The model prompt and token budget are the primary controls. This final guardrail
    prevents a single response from becoming a wall of text if a model ignores them.
    It only cuts at a natural sentence or line boundary and clearly indicates truncation.
    """
    reply = (reply or "").strip()
    if len(reply) <= character_limit:
        return reply

    candidate = reply[:character_limit]
    boundaries = [candidate.rfind("\n"), candidate.rfind(". "), candidate.rfind("! "), candidate.rfind("? ")]
    cut_at = max(boundaries)
    if cut_at < character_limit // 2:
        cut_at = character_limit
    return candidate[:cut_at].rstrip(" .!?\n") + "…"


def _is_memory_worthy(user_message):
    """Avoid an extra LLM call for greetings and one-off study updates.

    This deliberately has a high bar: missing a weak memory is safer than repeatedly
    storing temporary facts or spending tokens after every ordinary question.
    """
    import re

    text = (user_message or "").lower()
    patterns = (
        r"\bi (?:prefer|like|struggle|find|learn|study better)\b",
        r"\bmy (?:goal|target|exam|schedule|routine)\b",
        r"\bi want to (?:finish|complete|score|study|revise)\b",
        r"\bi(?:'m| am) (?:working|preparing|studying)\b",
        r"\b(?:every|usually|always|never)\b",
        r"\b(?:until|starting|from) \d{1,2}(?:st|nd|rd|th)?\b",
    )
    return len(text) >= 12 and any(re.search(pattern, text) for pattern in patterns)


def _normalise_memory(text):
    import re
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", "", (text or "").lower())).strip()


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

    raw = generate_text(
        MEMORY_EXTRACT_PROMPT, user_prompt, model=MEMORY_MODEL,
        max_tokens=300, reasoning_effort="none",
    )

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
    existing_normalised = {
        _normalise_memory("".join(t.get("plain_text", "") for t in m["properties"]["Memory"]["title"]))
        for m in (existing_memories or [])
    }
    saved = 0
    for mem in memories:
        if saved >= 3:
            break
        if isinstance(mem, dict) and isinstance(mem.get("memory"), str):
            memory_text = mem["memory"].strip()
            normalised = _normalise_memory(memory_text)
            if not normalised or len(memory_text) > 300 or normalised in existing_normalised:
                continue
            category = mem.get("category", "insight")
            if category not in valid_categories:
                category = "insight"
            source = mem.get("source")
            if source not in {"user-stated", "bot-inferred"}:
                source = "bot-inferred"
            save_memory(memory_text, category=category, source=source)
            existing_normalised.add(normalised)
            saved += 1
