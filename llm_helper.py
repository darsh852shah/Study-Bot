import os
import json
import requests

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
MODEL = "qwen/qwen3.8-27b"  # swap to "gemma2-9b-it" if you'd rather use Gemma


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


def generate_text(system_prompt, user_prompt, max_tokens=220):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.7,
    }
    r = requests.post(GROQ_URL, headers=headers, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


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
    even when asked for JSON only — strip it defensively before parsing."""
    if "<think>" in text and "</think>" in text:
        text = text.split("</think>", 1)[1]
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

    raw = generate_text(EXTRACT_SYSTEM_PROMPT, user_prompt, max_tokens=400)
    return _parse_json_object(raw)
