import os
import json
import re
import time
import requests

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Keep these configurable so a deployment can use the exact model IDs shown in its Groq
# console without editing code. The smaller model handles short classification; the newer
# Qwen model is reserved for the user-facing answer.
CLASSIFIER_MODEL = os.environ.get("GROQ_CLASSIFIER_MODEL", "openai/gpt-oss-20b")
ANSWER_MODEL = os.environ.get("GROQ_ANSWER_MODEL", "qwen/qwen3.8-27b")
MEMORY_MODEL = os.environ.get("GROQ_MEMORY_MODEL", CLASSIFIER_MODEL)
LOG_EXTRACTION_MODEL = os.environ.get("GROQ_LOG_EXTRACTION_MODEL", MEMORY_MODEL)
ANSWER_MAX_TOKENS = int(os.environ.get("GROQ_ANSWER_MAX_TOKENS", "450"))
LOG_EXTRACTION_MAX_TOKENS = int(os.environ.get("GROQ_LOG_EXTRACTION_MAX_TOKENS", "384"))
MEMORY_MAX_TOKENS = int(os.environ.get("GROQ_MEMORY_MAX_TOKENS", "220"))


def trim_prompt_text(text, max_chars):
    """Bound context sent to an LLM while keeping the beginning of each source intact."""
    text = (text or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit("\n", 1)[0].rstrip() + "\n[context truncated]"


def load_plan_summary():
    """Prefers the live Notion page; falls back to the static file if the fetch fails."""
    try:
        from notion_helper import fetch_plan_text
        live = fetch_plan_text()
        if live:
            return live
    except Exception:
        pass
    with open("plan_summary.txt") as f:
        return f.read()


def format_lecture_stats(stats, max_chapters=6):
    """Turns notion_helper.get_lecture_stats()'s output into a compact text block for the prompt."""
    if not stats:
        return "No lecture tracker data available."
    lines = []
    for subject, s in stats.items():
        pct = round(100 * s["watched"] / s["total"], 1) if s["total"] else 0.0
        watched_hrs = round(s["watched_minutes"] / 60, 1)
        remaining_hrs = round(s["remaining_minutes"] / 60, 1)
        lines.append(
            f"{subject}: {s['watched']}/{s['total']} lectures watched ({pct}%) — "
            f"{watched_hrs}h watched, {remaining_hrs}h remaining"
        )
        remaining = s["not_started_chapters"][:max_chapters]
        if remaining:
            more = f" (+{len(s['not_started_chapters']) - max_chapters} more)" if len(s["not_started_chapters"]) > max_chapters else ""
            lines.append(f"  Not started, in syllabus order (next up first): {', '.join(remaining)}{more}")
    return "\n".join(lines)


CLASSIFY_SYSTEM_PROMPT = """Classify a CA Final student's Telegram message into exactly one category. Reply with ONLY one word: LOG or QUERY.

LOG: the message is reporting what they did today — activities, hours studied, mood, energy, a win, a distraction, or a fix for tomorrow. Usually past tense, a recap of the day. Also LOG if it's a plain correction/addition to a log already in progress (e.g. "make it 3 hours", "mood was more like a 4").

QUERY: the message is a question, or is asking for help, analysis, or a plan — about their study plan, deadlines, progress, lecture tracker, how they're doing, what to do next, how to catch up, or how to improve. Also QUERY for greetings, small talk, or anything that isn't clearly a same-day activity recap.

When genuinely ambiguous, prefer LOG only if it clearly describes activities/hours already done today; otherwise QUERY."""


_GREETING_SHORTCUTS = {
    "hi", "hii", "hey", "heyy", "hello", "yo", "sup", "hola",
    "good morning", "good evening", "good afternoon", "gm", "morning",
}


def _looks_like_log(text):
    """Recognize common study recaps without spending an LLM call."""
    lowered = text.lower()
    has_hours = bool(re.search(r"\b\d+(?:\.\d+)?\s*(?:hours?|hrs?|h)\b", lowered))
    has_activity = any(
        re.search(rf"\b{re.escape(activity.lower())}\b", lowered)
        for activity in ("SPOM", "ITT", "GMCS", "FR", "AFM", "DT", "IDT", "Audit", "IBS", "Revision", "Mock")
    )
    has_recap_marker = bool(
        re.search(r"\b(studied|did|finished|completed|watched|revised|covered|spent)\b", lowered)
    )
    has_log_field = bool(
        re.search(r"\b(?:mood|energy)\s*(?:was|is|:)?\s*[1-5]\b", lowered)
        or re.search(r"\b(?:distracted|focus|win|tomorrow)\b", lowered)
    )
    return has_hours and (has_activity or has_recap_marker or has_log_field)


def classify_intent(text):
    """Decides whether a free-text message is a study-log entry or a conversational
    question/plan request.

    Cheap greetings/questions are caught with a direct check first — reasoning models like
    gpt-oss-120b can sometimes burn a small token budget on internal reasoning before ever
    emitting LOG/QUERY, which used to make short ambiguous messages default to "log" and
    silently start an empty log draft. Defaults to "query" on any failure or ambiguity: a
    real log message misread as a query just gets a conversational reply (recoverable by
    re-sending or using /log), whereas a real question misread as "log" used to get trapped
    answering log-draft prompts instead — a worse outcome, so the safer default flipped."""
    stripped = text.strip().lower().strip(" !.?")
    if stripped in _GREETING_SHORTCUTS or text.strip().endswith("?"):
        return "query"
    if _looks_like_log(text):
        return "log"

    try:
        raw = generate_text(
            CLASSIFY_SYSTEM_PROMPT, f'Message: "{text}"', model=CLASSIFIER_MODEL,
            max_tokens=12, reasoning_effort="none",
        )
    except Exception:
        return "log" if _looks_like_log(text) else "query"

    upper = raw.strip().upper()
    if "LOG" in upper and "QUERY" not in upper:
        return "log"
    return "query"


def format_logs(entries):
    """Turns Notion query results into a compact text block for the prompt."""
    if not entries:
        return "No logs yet."
    lines = []
    for e in entries:
        props = e["properties"]
        date_obj = props.get("Date", {}).get("date")
        date = date_obj["start"] if date_obj else "?"
        hrs = props["Time effective (hrs)"]["number"]
        mood = props["Mood (1–5)"]["number"]
        energy = props["Energy (1–5)"]["number"]
        win = "".join(t.get("plain_text", "") for t in props["Win"]["rich_text"])
        broke = ", ".join(t["name"] for t in props["What broke focus"]["multi_select"])
        breakdown = "".join(t.get("plain_text", "") for t in props.get("Activity Breakdown", {}).get("rich_text", []))
        lines.append(
            f"{date}: {hrs if hrs is not None else '—'}h effective, "
            f"mood {mood if mood is not None else '—'}/5, energy {energy if energy is not None else '—'}/5, "
            f"breakdown: {breakdown or '—'}, win: {win or '—'}, broke focus: {broke or '—'}"
        )
    return "\n".join(lines)


def generate_text(system_prompt, user_prompt, model=ANSWER_MODEL, max_tokens=220, reasoning_effort="none"):
    """Generate text with the requested Groq model.

    ``reasoning_effort`` support varies by Groq model; the configured models use only
    "none" or "default". Set the ``GROQ_*_MODEL`` environment variables to exact model
    IDs from the Groq console when they differ from the defaults.
    "none" disables reasoning (faster, lower quality — good for classify_intent).
    "default" enables reasoning. Keep it opt-in because hidden reasoning tokens count against
    the Qwen quota and are not useful for short bot replies or JSON extraction.
    Passing any other value ("low", "medium", "high") causes a 400 Bad Request from the Groq API.

    Retries automatically on 429 (rate limit) with exponential backoff — Groq's free tier
    has tight limits (~30 req/min) and the bot can hit them with back-to-back calls."""
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "reasoning_effort": reasoning_effort,
    }

    max_retries = 3
    for attempt in range(max_retries + 1):
        r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=60)
        if r.status_code == 429 and attempt < max_retries:
            # Respect Retry-After header if provided, otherwise exponential backoff
            wait = float(r.headers.get("Retry-After", 2 ** (attempt + 1)))
            time.sleep(wait)
            continue
        r.raise_for_status()
        break

    content = _strip_reasoning((r.json()["choices"][0]["message"].get("content") or "").strip())
    if not content:
        raise RuntimeError(
            "Model returned empty content — likely ran out of tokens while reasoning "
            "before writing an answer. Try a higher max_tokens."
        )
    return content


EXTRACT_SYSTEM_PROMPT = """You extract structured study-log data from a CA Final student's message. The message may be typed text or a transcribed voice note, and may be casual, rambling, or use filler words (voice transcripts often do).

Valid activity names — fuzzy-match whatever the person mentions to the closest one of these (use "Other" only if truly nothing fits): SPOM, ITT, GMCS, FR, AFM, DT, IDT, Audit, IBS, Revision, Mock, Other.

Valid "what broke focus" options — fuzzy-match to the closest one (free text is fine if none fit): Phone / scrolling, Hunger / low energy, Noise / people, Anxiety / overthinking, Hard topic / confusion, Sleepiness, Planning too long, Other.

Output ONLY a single JSON object — no markdown fences, no <think> tags, no commentary before or after. Exact schema:
{
  "breakdown": "Activity:hours,Activity:hours" (string) or null if no activities/hours were mentioned at all,
  "mood": integer 1-5, or null if genuinely not mentioned or implied,
  "energy": integer 1-5, or null if genuinely not mentioned or implied,
  "win": short string, or "" if not mentioned,
  "broke": short string (ideally one of the valid options above), or "" if not mentioned,
  "fix": short string, or "" if not mentioned
}

You may reasonably infer mood/energy from tone and wording (e.g. "felt pretty good today" -> mood 4), but never invent specific hours or activities that weren't mentioned. When updating a previous JSON based on a correction message, keep every field the correction doesn't address unchanged, and only change what the correction implies."""


def _strip_reasoning(text):
    """Qwen3 and similar reasoning models sometimes wrap chain-of-thought in <think> tags
    even when asked for JSON only — strip it defensively before parsing.
    Also handles truncated blocks where </think> is missing (model hit max_tokens
    while still reasoning and never wrote the closing tag or any real answer)."""
    if "<think>" in text:
        if "</think>" in text:
            text = text.split("</think>", 1)[1]
        else:
            # Truncated: no closing tag → everything from <think> onward is reasoning.
            # Keep only any content that appeared before the tag (usually empty).
            text = text.split("<think>", 1)[0]
    return text.strip()


def _parse_json_object(raw):
    text = _strip_reasoning(raw)
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in the model's response")
    return json.loads(text[start:end + 1])


def extract_log_fields(message_text=None, previous_draft=None, correction_text=None):
    """Extracts (or revises) structured log fields from free text/voice-transcribed text via the LLM.
    Pass message_text for a fresh extraction, or previous_draft + correction_text to revise an
    existing draft based on a follow-up message. Returns a dict matching EXTRACT_SYSTEM_PROMPT's schema."""
    if previous_draft is not None and correction_text is not None:
        user_prompt = (
            f"Previous understanding (JSON): {json.dumps(previous_draft)}\n\n"
            f'The user\'s follow-up/correction message: "{correction_text}"\n\n'
            "Output the updated JSON object only."
        )
    else:
        user_prompt = f'User\'s message: "{message_text}"\n\nExtract the JSON object.'

    raw = generate_text(
        EXTRACT_SYSTEM_PROMPT, user_prompt, model=LOG_EXTRACTION_MODEL,
        max_tokens=LOG_EXTRACTION_MAX_TOKENS, reasoning_effort="none",
    )
    return _parse_json_object(raw)
