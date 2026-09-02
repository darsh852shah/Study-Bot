import os
import re
import requests
from datetime import datetime, timezone, timedelta

NOTION_API_KEY = os.environ["NOTION_API_KEY"]
NOTION_DATABASE_ID = os.environ["NOTION_DATABASE_ID"]
NOTION_LECTURE_DB_ID = os.environ.get("NOTION_LECTURE_DB_ID")  # "Lecture Tracker" database — optional, powers query_handler
NOTION_MEMORY_DB_ID = os.environ.get("NOTION_MEMORY_DB_ID")    # "Bot Memory" database — optional, gives the bot long-term memory
NOTION_VERSION = "2022-06-28"

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": NOTION_VERSION,
    "Content-Type": "application/json",
}

IST = timezone(timedelta(hours=5, minutes=30))

VALID_BROKE_OPTIONS = [
    "Phone / scrolling", "Hunger / low energy", "Noise / people",
    "Anxiety / overthinking", "Hard topic / confusion", "Sleepiness",
    "Planning too long", "Other",
]


def today_ist():
    return datetime.now(IST)


VALID_ACTIVITY_OPTIONS = [
    "SPOM", "ITT", "GMCS", "FR", "AFM", "DT", "IDT", "Audit", "IBS", "Revision", "Mock", "Other",
]


def _match_multiselect(raw_text, valid_options, split_on_slash=False):
    """Fuzzy-matches free text against a database's real multi-select options."""
    matched = []
    if raw_text and raw_text.strip():
        lowered = raw_text.lower()
        for opt in valid_options:
            key = opt.lower().split(" /")[0].strip() if split_on_slash else opt.lower()
            if key in lowered:
                matched.append(opt)
        if not matched:
            matched = ["Other"]
    return matched


def parse_activity_breakdown(breakdown):
    """Parses a string like 'AFM:1,ITT:6' into (total_hours, matched_activity_names, readable_text)."""
    total_hours = 0.0
    activity_names = []
    breakdown_parts = []
    for chunk in (breakdown or "").split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        name, hrs_str = chunk.split(":", 1)
        name = name.strip()
        try:
            hrs = float(hrs_str.strip())
        except ValueError:
            continue
        total_hours += hrs
        matched = _match_multiselect(name, VALID_ACTIVITY_OPTIONS, split_on_slash=False)
        for m in matched:
            if m not in activity_names:
                activity_names.append(m)
        breakdown_parts.append(f"{name}: {hrs}h")
    return round(total_hours, 2), activity_names, ", ".join(breakdown_parts)


def match_broke_options(broke):
    """Fuzzy-matches free text against the valid 'What broke focus' multi-select options."""
    return _match_multiselect(broke, VALID_BROKE_OPTIONS, split_on_slash=True)


def preview_entry(breakdown, mood, energy, win, broke, fix):
    """Computes the same derived values create_log_entry would write, without writing anything.
    Used to show the user a confirmation before saving a free-text or voice-derived log."""
    total_hours, activity_names, breakdown_text = parse_activity_breakdown(breakdown)
    broke_list = match_broke_options(broke)
    return {
        "total_hours": total_hours,
        "activities": activity_names,
        "breakdown_text": breakdown_text,
        "mood": mood,
        "energy": energy,
        "win": win or "",
        "broke": ", ".join(broke_list) if broke_list else "",
        "fix": fix or "",
    }


def create_log_entry(breakdown, mood, energy, win, broke, fix):
    """Creates one row in the Daily Log (DB). `breakdown` is a string like
    'AFM:1,ITT:6' — parsed into total hours, the Activity multi-select, and
    a readable Activity Breakdown text field."""
    today = today_ist()
    date_str = today.strftime("%Y-%m-%d")
    day_str = today.strftime("%A, %d %b")

    broke_list = match_broke_options(broke)
    total_hours, activity_names, breakdown_text = parse_activity_breakdown(breakdown)

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Day": {"title": [{"text": {"content": day_str}}]},
            "Date": {"date": {"start": date_str}},
            "Time effective (hrs)": {"number": round(total_hours, 2)},
            "Mood (1–5)": {"number": int(float(mood))},
            "Energy (1–5)": {"number": int(float(energy))},
            "Win": {"rich_text": [{"text": {"content": win}}]},
            "What broke focus": {"multi_select": [{"name": t} for t in broke_list]},
            "Fix for tomorrow": {"rich_text": [{"text": {"content": fix}}]},
            "Activity": {"multi_select": [{"name": t} for t in activity_names]},
            "Activity Breakdown": {"rich_text": [{"text": {"content": breakdown_text}}]},
        },
    }
    r = requests.post("https://api.notion.com/v1/pages", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()


def fetch_plan_text(max_chars=16000):
    """Fetches the live Master Plan Notion page and flattens it to plain text for LLM context.
    max_chars was 4000 — nowhere near enough for this plan (dashboard, 3 tables, phase
    checklists, daily template, non-negotiables, open items all add up to well over that),
    so the LLM was silently only ever seeing the first ~40% of the page and never reaching the
    non-negotiables/deadlines/open-items sections. 16000 chars comfortably fits the whole
    document today with room for it to grow as a living doc, while still guarding against
    unbounded growth blowing up the prompt."""
    page_id = os.environ.get("NOTION_PAGE_ID")
    if not page_id:
        return None

    lines = []

    def extract_rich_text(rich_text_list):
        return "".join(t.get("plain_text", "") for t in rich_text_list)

    def block_to_text(block):
        btype = block["type"]
        data = block.get(btype, {})
        if "rich_text" in data:
            return extract_rich_text(data["rich_text"])
        if btype == "table_row":
            return " | ".join(extract_rich_text(c) for c in data.get("cells", []))
        return ""

    def walk(block_id):
        if sum(len(l) for l in lines) > max_chars:
            return
        cursor = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            r = requests.get(
                f"https://api.notion.com/v1/blocks/{block_id}/children",
                headers=HEADERS, params=params,
            )
            r.raise_for_status()
            data = r.json()
            for block in data["results"]:
                btype = block["type"]
                if btype == "child_database":
                    continue  # skip the Daily Log DB itself — too large, and covered by recent-logs context separately
                text = block_to_text(block)
                if text.strip():
                    lines.append(text.strip())
                if block.get("has_children") and btype != "child_page":
                    walk(block["id"])
            cursor = data.get("next_cursor")
            if not data.get("has_more"):
                break

    try:
        walk(page_id)
    except Exception:
        return None

    full = "\n".join(lines)
    return full[:max_chars] if full else None


def get_recent_entries(days=5):
    """Fetches the last N logged days, most recent first, for LLM context."""
    payload = {
        "sorts": [{"property": "Date", "direction": "descending"}],
        "page_size": days,
    }
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=HEADERS, json=payload,
    )
    r.raise_for_status()
    return r.json().get("results", [])


_DURATION_RE = re.compile(r"(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+)\s*m)?", re.IGNORECASE)


def parse_duration_minutes(duration_text):
    """Parses your Lecture Tracker's Duration text (e.g. '1h 49m', '50m', '2h') into
    total minutes. Returns 0 for blank/unrecognized text rather than raising, since a
    stats rollup shouldn't die over one malformed row."""
    if not duration_text or not duration_text.strip():
        return 0
    match = _DURATION_RE.search(duration_text.strip())
    if not match or (not match.group(1) and not match.group(2)):
        return 0
    hours = float(match.group(1)) if match.group(1) else 0.0
    minutes = int(match.group(2)) if match.group(2) else 0
    return round(hours * 60 + minutes)


def _chapter_sort_key(chapter_name):
    """Chapters are numbered '00 ...', '1. ...' etc in real syllabus order — sort on that so
    'what's next' reflects the actual sequence, not raw Notion query order (which is
    effectively random and previously made the bot invent a nonsensical order). Chapters with
    no leading number (e.g. 'AFM Theory', a standalone/supplementary chapter) sort after every
    numbered chapter, in the order they were first encountered."""
    m = re.match(r"^(\d+)", chapter_name or "")
    return (0, int(m.group(1))) if m else (1, 0)


def get_lecture_stats():
    """Fetches every row of the Lecture Tracker DB and rolls it up per Subject (FR/AFM):
    lecture counts (Watched vs Not started), total watched/remaining time (parsed from the
    Duration column, e.g. '1h 49m'), and which chapters are still untouched — so the query
    handler can do real pacing math (e.g. hours of FR left vs days to Nov 30), not just a
    lecture count. Returns None if NOTION_LECTURE_DB_ID isn't set, so this stays optional."""
    if not NOTION_LECTURE_DB_ID:
        return None

    results = []
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_LECTURE_DB_ID}/query",
            headers=HEADERS, json=payload,
        )
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("results", []))
        cursor = data.get("next_cursor")
        if not data.get("has_more"):
            break

    stats = {}
    for page in results:
        props = page["properties"]
        subject = (props.get("Subject", {}).get("select") or {}).get("name") or "Other"
        status = (props.get("Status", {}).get("select") or {}).get("name") or "Not started"
        chapter = "".join(t.get("plain_text", "") for t in props.get("Chapter Name", {}).get("title", []))
        duration_text = "".join(t.get("plain_text", "") for t in props.get("Duration", {}).get("rich_text", []))
        minutes = parse_duration_minutes(duration_text)

        s = stats.setdefault(subject, {
            "watched": 0, "total": 0,
            "watched_minutes": 0, "remaining_minutes": 0,
            "not_started_chapters": [],
        })
        s["total"] += 1
        if status == "Watched":
            s["watched"] += 1
            s["watched_minutes"] += minutes
        else:
            s["remaining_minutes"] += minutes
            if chapter and chapter not in s["not_started_chapters"]:
                s["not_started_chapters"].append(chapter)

    for s in stats.values():
        s["not_started_chapters"].sort(key=_chapter_sort_key)

    return stats


def _normalize_lecture_text(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def find_lecture_matches(subject, lecture_query):
    """Fuzzy-but-safe match of lecture_query (e.g. 'Class 5', 'D18-P1') against the Lecture
    Tracker's 'Lecture' text property, scoped to one Subject (FR or AFM). Matching is exact
    after normalizing case/spaces/punctuation — NOT substring — because a substring match
    would let 'Class 5' wrongly match a row literally named 'Class 50'. Returns a list of
    {"page_id", "chapter", "lecture", "duration", "status"} dicts: for FR this is at most one
    (D#-P# labels are unique), for AFM it can be several (Class N repeats across chapters)."""
    if not NOTION_LECTURE_DB_ID:
        return []
    target = _normalize_lecture_text(lecture_query)
    if not target:
        return []

    results = []
    cursor = None
    while True:
        payload = {
            "filter": {"property": "Subject", "select": {"equals": subject}},
            "page_size": 100,
        }
        if cursor:
            payload["start_cursor"] = cursor
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_LECTURE_DB_ID}/query",
            headers=HEADERS, json=payload,
        )
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("results", []))
        cursor = data.get("next_cursor")
        if not data.get("has_more"):
            break

    matches = []
    for page in results:
        props = page["properties"]
        lecture_text = "".join(t.get("plain_text", "") for t in props.get("Lecture", {}).get("rich_text", []))
        if _normalize_lecture_text(lecture_text) != target:
            continue
        chapter = "".join(t.get("plain_text", "") for t in props.get("Chapter Name", {}).get("title", []))
        duration = "".join(t.get("plain_text", "") for t in props.get("Duration", {}).get("rich_text", []))
        status = (props.get("Status", {}).get("select") or {}).get("name") or "Not started"
        matches.append({
            "page_id": page["id"],
            "chapter": chapter,
            "lecture": lecture_text,
            "duration": duration,
            "status": status,
        })
    return matches


def mark_lecture_watched(page_id):
    """Marks one Lecture Tracker row Watched with today's date. Best-effort — raises on
    failure so the caller (poll_log.py) can tell the user the daily log saved fine but the
    tracker update itself didn't, rather than silently losing it."""
    date_str = today_ist().strftime("%Y-%m-%d")
    payload = {
        "properties": {
            "Status": {"select": {"name": "Watched"}},
            "Date completed": {"date": {"start": date_str}},
        }
    }
    r = requests.patch(f"https://api.notion.com/v1/pages/{page_id}", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()


def get_today_entry():
    """Fetches today's row (if any) from the Daily Log (DB)."""
    date_str = today_ist().strftime("%Y-%m-%d")
    payload = {"filter": {"property": "Date", "date": {"equals": date_str}}}
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=HEADERS, json=payload,
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None


# ─── Bot Memory (long-term) ──────────────────────────────────────────────────


def get_memories(limit=20):
    """Fetches the most recent memories from the Bot Memory database, newest first.
    Returns None if NOTION_MEMORY_DB_ID isn't set (feature is optional)."""
    if not NOTION_MEMORY_DB_ID:
        return None
    payload = {
        "sorts": [{"property": "Date", "direction": "descending"}],
        "page_size": limit,
    }
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_MEMORY_DB_ID}/query",
        headers=HEADERS, json=payload,
    )
    r.raise_for_status()
    return r.json().get("results", [])


def save_memory(memory_text, category="insight", source="bot-inferred"):
    """Saves a single memory to the Bot Memory database. Does nothing if
    NOTION_MEMORY_DB_ID isn't set."""
    if not NOTION_MEMORY_DB_ID:
        return None
    date_str = today_ist().strftime("%Y-%m-%d")
    payload = {
        "parent": {"database_id": NOTION_MEMORY_DB_ID},
        "properties": {
            "Memory": {"title": [{"text": {"content": memory_text}}]},
            "Category": {"select": {"name": category}},
            "Source": {"select": {"name": source}},
            "Date": {"date": {"start": date_str}},
        },
    }
    r = requests.post("https://api.notion.com/v1/pages", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()


def format_memories(memories):
    """Turns get_memories() output into a compact text block for injection into LLM prompts."""
    if not memories:
        return "No long-term memories stored yet."
    lines = []
    for m in memories:
        props = m["properties"]
        text = "".join(t.get("plain_text", "") for t in props["Memory"]["title"])
        cat = (props.get("Category", {}).get("select") or {}).get("name", "")
        date_obj = props.get("Date", {}).get("date")
        date = date_obj["start"] if date_obj else "?"
        lines.append(f"[{cat}] {text} ({date})")
    return "\n".join(lines)
    """Fuzzy-matches free text against a database's real multi-select options."""
    matched = []
    if raw_text and raw_text.strip():
        lowered = raw_text.lower()
        for opt in valid_options:
            key = opt.lower().split(" /")[0].strip() if split_on_slash else opt.lower()
            if key in lowered:
                matched.append(opt)
        if not matched:
            matched = ["Other"]
    return matched


def parse_activity_breakdown(breakdown):
    """Parses a string like 'AFM:1,ITT:6' into (total_hours, matched_activity_names, readable_text)."""
    total_hours = 0.0
    activity_names = []
    breakdown_parts = []
    for chunk in (breakdown or "").split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        name, hrs_str = chunk.split(":", 1)
        name = name.strip()
        try:
            hrs = float(hrs_str.strip())
        except ValueError:
            continue
        total_hours += hrs
        matched = _match_multiselect(name, VALID_ACTIVITY_OPTIONS, split_on_slash=False)
        for m in matched:
            if m not in activity_names:
                activity_names.append(m)
        breakdown_parts.append(f"{name}: {hrs}h")
    return round(total_hours, 2), activity_names, ", ".join(breakdown_parts)


def match_broke_options(broke):
    """Fuzzy-matches free text against the valid 'What broke focus' multi-select options."""
    return _match_multiselect(broke, VALID_BROKE_OPTIONS, split_on_slash=True)


def preview_entry(breakdown, mood, energy, win, broke, fix):
    """Computes the same derived values create_log_entry would write, without writing anything.
    Used to show the user a confirmation before saving a free-text or voice-derived log."""
    total_hours, activity_names, breakdown_text = parse_activity_breakdown(breakdown)
    broke_list = match_broke_options(broke)
    return {
        "total_hours": total_hours,
        "activities": activity_names,
        "breakdown_text": breakdown_text,
        "mood": mood,
        "energy": energy,
        "win": win or "",
        "broke": ", ".join(broke_list) if broke_list else "",
        "fix": fix or "",
    }


def create_log_entry(breakdown, mood, energy, win, broke, fix):
    """Creates one row in the Daily Log (DB). `breakdown` is a string like
    'AFM:1,ITT:6' — parsed into total hours, the Activity multi-select, and
    a readable Activity Breakdown text field."""
    today = today_ist()
    date_str = today.strftime("%Y-%m-%d")
    day_str = today.strftime("%A, %d %b")

    broke_list = match_broke_options(broke)
    total_hours, activity_names, breakdown_text = parse_activity_breakdown(breakdown)

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": {
            "Day": {"title": [{"text": {"content": day_str}}]},
            "Date": {"date": {"start": date_str}},
            "Time effective (hrs)": {"number": round(total_hours, 2)},
            "Mood (1–5)": {"number": int(float(mood))},
            "Energy (1–5)": {"number": int(float(energy))},
            "Win": {"rich_text": [{"text": {"content": win}}]},
            "What broke focus": {"multi_select": [{"name": t} for t in broke_list]},
            "Fix for tomorrow": {"rich_text": [{"text": {"content": fix}}]},
            "Activity": {"multi_select": [{"name": t} for t in activity_names]},
            "Activity Breakdown": {"rich_text": [{"text": {"content": breakdown_text}}]},
        },
    }
    r = requests.post("https://api.notion.com/v1/pages", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()


def fetch_plan_text(max_chars=16000):
    """Fetches the live Master Plan Notion page and flattens it to plain text for LLM context.
    max_chars was 4000 — nowhere near enough for this plan (dashboard, 3 tables, phase
    checklists, daily template, non-negotiables, open items all add up to well over that),
    so the LLM was silently only ever seeing the first ~40% of the page and never reaching the
    non-negotiables/deadlines/open-items sections. 16000 chars comfortably fits the whole
    document today with room for it to grow as a living doc, while still guarding against
    unbounded growth blowing up the prompt."""
    page_id = os.environ.get("NOTION_PAGE_ID")
    if not page_id:
        return None

    lines = []

    def extract_rich_text(rich_text_list):
        return "".join(t.get("plain_text", "") for t in rich_text_list)

    def block_to_text(block):
        btype = block["type"]
        data = block.get(btype, {})
        if "rich_text" in data:
            return extract_rich_text(data["rich_text"])
        if btype == "table_row":
            return " | ".join(extract_rich_text(c) for c in data.get("cells", []))
        return ""

    def walk(block_id):
        if sum(len(l) for l in lines) > max_chars:
            return
        cursor = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            r = requests.get(
                f"https://api.notion.com/v1/blocks/{block_id}/children",
                headers=HEADERS, params=params,
            )
            r.raise_for_status()
            data = r.json()
            for block in data["results"]:
                btype = block["type"]
                if btype == "child_database":
                    continue  # skip the Daily Log DB itself — too large, and covered by recent-logs context separately
                text = block_to_text(block)
                if text.strip():
                    lines.append(text.strip())
                if block.get("has_children") and btype != "child_page":
                    walk(block["id"])
            cursor = data.get("next_cursor")
            if not data.get("has_more"):
                break

    try:
        walk(page_id)
    except Exception:
        return None

    full = "\n".join(lines)
    return full[:max_chars] if full else None


def get_recent_entries(days=5):
    """Fetches the last N logged days, most recent first, for LLM context."""
    payload = {
        "sorts": [{"property": "Date", "direction": "descending"}],
        "page_size": days,
    }
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=HEADERS, json=payload,
    )
    r.raise_for_status()
    return r.json().get("results", [])


_DURATION_RE = re.compile(r"(?:(\d+(?:\.\d+)?)\s*h)?\s*(?:(\d+)\s*m)?", re.IGNORECASE)


def parse_duration_minutes(duration_text):
    """Parses your Lecture Tracker's Duration text (e.g. '1h 49m', '50m', '2h') into
    total minutes. Returns 0 for blank/unrecognized text rather than raising, since a
    stats rollup shouldn't die over one malformed row."""
    if not duration_text or not duration_text.strip():
        return 0
    match = _DURATION_RE.search(duration_text.strip())
    if not match or (not match.group(1) and not match.group(2)):
        return 0
    hours = float(match.group(1)) if match.group(1) else 0.0
    minutes = int(match.group(2)) if match.group(2) else 0
    return round(hours * 60 + minutes)


def get_lecture_stats():
    """Fetches every row of the Lecture Tracker DB and rolls it up per Subject (FR/AFM):
    lecture counts (Watched vs Not started), total watched/remaining time (parsed from the
    Duration column, e.g. '1h 49m'), and which chapters are still untouched — so the query
    handler can do real pacing math (e.g. hours of FR left vs days to Nov 30), not just a
    lecture count. Returns None if NOTION_LECTURE_DB_ID isn't set, so this stays optional."""
    if not NOTION_LECTURE_DB_ID:
        return None

    results = []
    cursor = None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_LECTURE_DB_ID}/query",
            headers=HEADERS, json=payload,
        )
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("results", []))
        cursor = data.get("next_cursor")
        if not data.get("has_more"):
            break

    stats = {}
    for page in results:
        props = page["properties"]
        subject = (props.get("Subject", {}).get("select") or {}).get("name") or "Other"
        status = (props.get("Status", {}).get("select") or {}).get("name") or "Not started"
        chapter = "".join(t.get("plain_text", "") for t in props.get("Chapter Name", {}).get("title", []))
        duration_text = "".join(t.get("plain_text", "") for t in props.get("Duration", {}).get("rich_text", []))
        minutes = parse_duration_minutes(duration_text)

        s = stats.setdefault(subject, {
            "watched": 0, "total": 0,
            "watched_minutes": 0, "remaining_minutes": 0,
            "not_started_chapters": [],
        })
        s["total"] += 1
        if status == "Watched":
            s["watched"] += 1
            s["watched_minutes"] += minutes
        else:
            s["remaining_minutes"] += minutes
            if chapter and chapter not in s["not_started_chapters"]:
                s["not_started_chapters"].append(chapter)

    return stats


def _normalize_lecture_text(text):
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def find_lecture_matches(subject, lecture_query):
    """Fuzzy-but-safe match of lecture_query (e.g. 'Class 5', 'D18-P1') against the Lecture
    Tracker's 'Lecture' text property, scoped to one Subject (FR or AFM). Matching is exact
    after normalizing case/spaces/punctuation — NOT substring — because a substring match
    would let 'Class 5' wrongly match a row literally named 'Class 50'. Returns a list of
    {"page_id", "chapter", "lecture", "duration", "status"} dicts: for FR this is at most one
    (D#-P# labels are unique), for AFM it can be several (Class N repeats across chapters)."""
    if not NOTION_LECTURE_DB_ID:
        return []
    target = _normalize_lecture_text(lecture_query)
    if not target:
        return []

    results = []
    cursor = None
    while True:
        payload = {
            "filter": {"property": "Subject", "select": {"equals": subject}},
            "page_size": 100,
        }
        if cursor:
            payload["start_cursor"] = cursor
        r = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_LECTURE_DB_ID}/query",
            headers=HEADERS, json=payload,
        )
        r.raise_for_status()
        data = r.json()
        results.extend(data.get("results", []))
        cursor = data.get("next_cursor")
        if not data.get("has_more"):
            break

    matches = []
    for page in results:
        props = page["properties"]
        lecture_text = "".join(t.get("plain_text", "") for t in props.get("Lecture", {}).get("rich_text", []))
        if _normalize_lecture_text(lecture_text) != target:
            continue
        chapter = "".join(t.get("plain_text", "") for t in props.get("Chapter Name", {}).get("title", []))
        duration = "".join(t.get("plain_text", "") for t in props.get("Duration", {}).get("rich_text", []))
        status = (props.get("Status", {}).get("select") or {}).get("name") or "Not started"
        matches.append({
            "page_id": page["id"],
            "chapter": chapter,
            "lecture": lecture_text,
            "duration": duration,
            "status": status,
        })
    return matches


def mark_lecture_watched(page_id):
    """Marks one Lecture Tracker row Watched with today's date. Best-effort — raises on
    failure so the caller (poll_log.py) can tell the user the daily log saved fine but the
    tracker update itself didn't, rather than silently losing it."""
    date_str = today_ist().strftime("%Y-%m-%d")
    payload = {
        "properties": {
            "Status": {"select": {"name": "Watched"}},
            "Date completed": {"date": {"start": date_str}},
        }
    }
    r = requests.patch(f"https://api.notion.com/v1/pages/{page_id}", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()


def get_today_entry():
    """Fetches today's row (if any) from the Daily Log (DB)."""
    date_str = today_ist().strftime("%Y-%m-%d")
    payload = {"filter": {"property": "Date", "date": {"equals": date_str}}}
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=HEADERS, json=payload,
    )
    r.raise_for_status()
    results = r.json().get("results", [])
    return results[0] if results else None


# ─── Bot Memory (long-term) ──────────────────────────────────────────────────


def get_memories(limit=20):
    """Fetches the most recent memories from the Bot Memory database, newest first.
    Returns None if NOTION_MEMORY_DB_ID isn't set (feature is optional)."""
    if not NOTION_MEMORY_DB_ID:
        return None
    payload = {
        "sorts": [{"property": "Date", "direction": "descending"}],
        "page_size": limit,
    }
    r = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_MEMORY_DB_ID}/query",
        headers=HEADERS, json=payload,
    )
    r.raise_for_status()
    return r.json().get("results", [])


def save_memory(memory_text, category="insight", source="bot-inferred"):
    """Saves a single memory to the Bot Memory database. Does nothing if
    NOTION_MEMORY_DB_ID isn't set."""
    if not NOTION_MEMORY_DB_ID:
        return None
    date_str = today_ist().strftime("%Y-%m-%d")
    payload = {
        "parent": {"database_id": NOTION_MEMORY_DB_ID},
        "properties": {
            "Memory": {"title": [{"text": {"content": memory_text}}]},
            "Category": {"select": {"name": category}},
            "Source": {"select": {"name": source}},
            "Date": {"date": {"start": date_str}},
        },
    }
    r = requests.post("https://api.notion.com/v1/pages", headers=HEADERS, json=payload)
    r.raise_for_status()
    return r.json()


def format_memories(memories):
    """Turns get_memories() output into a compact text block for injection into LLM prompts."""
    if not memories:
        return "No long-term memories stored yet."
    lines = []
    for m in memories:
        props = m["properties"]
        text = "".join(t.get("plain_text", "") for t in props["Memory"]["title"])
        cat = (props.get("Category", {}).get("select") or {}).get("name", "")
        date_obj = props.get("Date", {}).get("date")
        date = date_obj["start"] if date_obj else "?"
        lines.append(f"[{cat}] {text} ({date})")
    return "\n".join(lines)
