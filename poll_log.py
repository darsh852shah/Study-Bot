import json

from telegram_helper import get_updates, send_message, download_voice
from notion_helper import create_log_entry, preview_entry
from llm_helper import extract_log_fields
from stt_helper import transcribe_audio

OFFSET_FILE = "last_update_id.txt"
PENDING_FILE = "pending_log.json"

CONFIRM_WORDS = {"yes", "y", "yep", "yeah", "yup", "confirm", "save", "ok", "okay", "correct", "right", "sure"}
CANCEL_WORDS = {"no", "cancel", "discard", "nvm", "never mind", "scrap", "stop"}
NEW_LOG_TRIGGERS = ["new log", "new entry", "start over", "start fresh", "reset"]

STRICT_LOG_HELP = (
    "⚠️ Format: `/log activity:hrs,activity:hrs|mood|energy|win|broke|fix`\n"
    "_Example:_ `/log AFM:1,ITT:6|3|2|Watched 1 AFM lecture, sat ITT|Sleepiness|Sleep earlier`\n\n"
    "Or just send a normal sentence — or a voice note — describing your day instead."
)


# ---- persisted state (offset + any in-progress draft), committed back to the repo by the workflow ----

def get_last_offset():
    try:
        with open(OFFSET_FILE) as f:
            content = f.read().strip()
            return int(content) if content else None
    except FileNotFoundError:
        return None


def save_offset(offset):
    with open(OFFSET_FILE, "w") as f:
        f.write(str(offset))


def load_pending():
    try:
        with open(PENDING_FILE) as f:
            data = json.load(f)
            return data if data else None
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_pending(draft):
    with open(PENDING_FILE, "w") as f:
        json.dump(draft if draft else {}, f)


# ---- helpers ----

def missing_fields(draft):
    missing = []
    if not draft.get("breakdown"):
        missing.append("activity + hours")
    if draft.get("mood") is None:
        missing.append("mood")
    if draft.get("energy") is None:
        missing.append("energy")
    return missing


def format_confirmation(draft):
    preview = preview_entry(
        draft.get("breakdown") or "",
        draft.get("mood"),
        draft.get("energy"),
        draft.get("win") or "",
        draft.get("broke") or "",
        draft.get("fix") or "",
    )
    mood_str = draft.get("mood") if draft.get("mood") is not None else "?"
    energy_str = draft.get("energy") if draft.get("energy") is not None else "?"

    lines = ["Got it — here's what I understood:"]
    total_line = f"• Total: {preview['total_hours']}h"
    if preview["breakdown_text"]:
        total_line += f" ({preview['breakdown_text']})"
    lines.append(total_line)
    lines.append(f"• Mood: {mood_str}/5  |  Energy: {energy_str}/5")
    lines.append(f"• Win: {preview['win'] or '—'}")
    lines.append(f"• Broke focus: {preview['broke'] or '—'}")
    lines.append(f"• Fix for tomorrow: {preview['fix'] or '—'}")

    missing = missing_fields(draft)
    if missing:
        lines.append(f"\n⚠️ Couldn't tell your {', '.join(missing)} — reply with that and I'll fill it in.")
    else:
        lines.append("\nReply *yes* to save, or just tell me what to fix.")
    return "\n".join(lines)


def save_draft(draft):
    result = create_log_entry(
        draft.get("breakdown") or "",
        draft.get("mood"),
        draft.get("energy"),
        draft.get("win") or "",
        draft.get("broke") or "",
        draft.get("fix") or "",
    )
    total = result["properties"]["Time effective (hrs)"]["number"]
    send_message(f"✅ Saved — {total}h logged for today.")


def handle_strict_log(body):
    parts = [p.strip() for p in body.split("|")]
    if len(parts) != 6:
        send_message(STRICT_LOG_HELP)
        return
    breakdown, mood, energy, win, broke, fix = parts
    try:
        result = create_log_entry(breakdown, mood, energy, win, broke, fix)
        total = result["properties"]["Time effective (hrs)"]["number"]
        send_message(f"✅ Logged: {breakdown} (total {total}h), mood {mood}/5, energy {energy}/5")
    except Exception as e:
        send_message(f"⚠️ Couldn't save that log: {e}")


def match_new_log_trigger(incoming_text):
    """If the message starts with an explicit 'new log' style phrase, returns whatever text
    comes after it (possibly empty). Returns None if the message isn't a new-log trigger."""
    lowered = incoming_text.lower()
    for trig in NEW_LOG_TRIGGERS:
        if lowered == trig:
            return ""
        if lowered.startswith(trig) and len(lowered) > len(trig) and not lowered[len(trig)].isalnum():
            return incoming_text[len(trig):].strip(" :,-–—.")
    return None


# ---- core per-update logic ----

def process_update(update, pending, incoming_text=None):
    """Returns the new pending draft (or None) after handling one Telegram update.
    Pass `incoming_text` if the caller (e.g. app.py's webhook) already extracted/transcribed
    the message text, so voice notes aren't sent to Groq's Whisper twice."""
    message = update.get("message", {})
    text = message.get("text")
    voice = message.get("voice")

    if incoming_text is None:
        if voice:
            try:
                audio_bytes = download_voice(voice["file_id"])
                incoming_text = transcribe_audio(audio_bytes)
            except Exception as e:
                send_message(f"⚠️ Couldn't transcribe that voice note: {e}")
                return pending
        elif text:
            incoming_text = text.strip()

    if not incoming_text:
        return pending  # sticker, photo, empty message, etc. — nothing to do

    # Strict structured command always works and bypasses the confirmation flow entirely
    if incoming_text.startswith("/log"):
        handle_strict_log(incoming_text[len("/log"):].strip())
        return None  # an explicit command supersedes any stale pending draft

    if incoming_text.startswith("/"):
        return pending  # unrecognized command — ignore

    lowered = incoming_text.lower().strip(" .!")

    # Explicit "new log" / "start over" style phrase — discards any stale pending draft.
    # Checked before the pending-draft branch below so it works whether or not a draft exists.
    new_log_remainder = match_new_log_trigger(incoming_text)
    if new_log_remainder is not None:
        prefix = "Discarding the previous draft.\n\n" if pending else ""
        if new_log_remainder:
            try:
                draft = extract_log_fields(message_text=new_log_remainder)
            except Exception as e:
                send_message(f"{prefix}⚠️ Couldn't parse that ({e}). Try again, or use `/log ...`.")
                return None
            send_message(prefix + format_confirmation(draft))
            return draft
        send_message(prefix + "Starting fresh — send your log whenever you're ready (voice note or text).")
        return None

    if pending:
        if lowered in CONFIRM_WORDS:
            missing = missing_fields(pending)
            if missing:
                send_message(f"⚠️ Still missing: {', '.join(missing)}. Tell me those first and I'll save it.")
                return pending
            try:
                save_draft(pending)
            except Exception as e:
                send_message(f"⚠️ Couldn't save that log: {e}")
                return pending
            return None

        if lowered in CANCEL_WORDS:
            send_message("Discarded — send your log again whenever you're ready.")
            return None

        # Anything else while a draft is pending is treated as a correction/addition
        try:
            updated = extract_log_fields(previous_draft=pending, correction_text=incoming_text)
        except Exception as e:
            send_message(
                f"⚠️ Had trouble understanding that ({e}). Reply *yes* to save what I already had, "
                "or try rephrasing the correction."
            )
            return pending
        send_message(format_confirmation(updated))
        return updated

    # No pending draft — this is a fresh free-text or voice log
    try:
        draft = extract_log_fields(message_text=incoming_text)
    except Exception as e:
        send_message(
            f"⚠️ Couldn't parse that ({e}). Try `/log activity:hrs|mood|energy|win|broke|fix`, "
            "or rephrase in plain text or a voice note."
        )
        return None
    send_message(format_confirmation(draft))
    return draft


def main():
    offset = get_last_offset()
    pending = load_pending()
    updates = get_updates(offset=(offset + 1) if offset is not None else None)

    last_id = offset
    for update in updates:
        last_id = update["update_id"]
        pending = process_update(update, pending)

    if last_id is not None:
        save_offset(last_id)
    save_pending(pending)


if __name__ == "__main__":
    main()
