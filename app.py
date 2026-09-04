"""Always-on webhook server for Study-Bot.

Replaces poll_log.py's 15-min GitHub Actions polling with an instant Telegram webhook.
Deploy this as a small web service (Render/Railway/Fly.io all work unchanged) — GitHub
Actions can't do this part because it only runs on a timer, it can't sit and listen for
inbound HTTP requests.

Routing logic per incoming message:
  1. "/..." commands, or a log draft already awaiting confirmation -> existing log flow
     (poll_log.process_update), unchanged behavior.
  2. Otherwise -> classify_intent() decides "log" (fresh log attempt) vs "query"
     (a question / re-plan request), and routes accordingly.

State (pending log draft, recent message IDs, and a little chat memory for conversational
continuity) is stored in a local JSON file. This survives a restart on a single instance;
use shared durable storage before scaling the service to multiple instances.
"""

import os
import json
import threading

from flask import Flask, request, jsonify

from telegram_helper import send_message, download_voice
from llm_helper import classify_intent
from stt_helper import transcribe_audio
import poll_log
from query_handler import answer_query

app = Flask(__name__)

TELEGRAM_CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])
# Required: Telegram sends this header when the webhook was registered with secret_token.
# Refuse to start without it so a public endpoint cannot silently run unauthenticated.
WEBHOOK_SECRET = os.environ["TELEGRAM_WEBHOOK_SECRET"]

MAX_HISTORY = 6  # (role, text) turns kept for conversational continuity

STATE_FILE = os.environ.get("STUDY_BOT_STATE_FILE", "webhook_state.json")
MAX_PROCESSED_UPDATES = 1000
MAX_QUEUED_UPDATES = 20


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as state_file:
            saved = json.load(state_file)
        if not isinstance(saved, dict):
            raise ValueError("state is not an object")
        chat_history = saved.get("chat_history", [])
        processed_updates = saved.get("processed_updates", [])
        if not isinstance(chat_history, list):
            chat_history = []
        if not isinstance(processed_updates, list):
            processed_updates = []
        processed_updates = [update_id for update_id in processed_updates if isinstance(update_id, int)]
        return {
            "pending": saved.get("pending"),
            "chat_history": chat_history[-MAX_HISTORY:],
            "processed_updates": processed_updates[-MAX_PROCESSED_UPDATES:],
        return {
            "pending": saved.get("pending"),
            "chat_history": saved.get("chat_history", [])[-MAX_HISTORY:],
            "processed_updates": saved.get("processed_updates", [])[-MAX_PROCESSED_UPDATES:],
        }
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return {"pending": None, "chat_history": [], "processed_updates": []}


def save_state():
    temporary_file = f"{STATE_FILE}.tmp"
    with open(temporary_file, "w", encoding="utf-8") as state_file:
        json.dump(STATE, state_file)
    os.replace(temporary_file, STATE_FILE)


STATE = load_state()
STATE_LOCK = threading.Lock()  # Telegram can deliver updates in quick succession; keep them serialized
UPDATE_SLOTS = threading.BoundedSemaphore(MAX_QUEUED_UPDATES)


def add_to_history(role, text):
    STATE["chat_history"].append((role, text))
    del STATE["chat_history"][:-MAX_HISTORY]


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    if request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "bad secret token"}), 403

    update = request.get_json(silent=True)
    if not isinstance(update, dict):
        return jsonify({"ok": True})
    message = update.get("message")
    if not isinstance(message, dict):
        return jsonify({"ok": True})
    chat = message.get("chat")
    if not isinstance(chat, dict):
        return jsonify({"ok": True})
    chat_id = str(chat.get("id", ""))
    update_id = update.get("update_id")

    # This endpoint is a public URL — only ever act on messages from the owner's chat.
    if not isinstance(update_id, int) or not chat_id or chat_id != TELEGRAM_CHAT_ID:
        return jsonify({"ok": True})

    if not UPDATE_SLOTS.acquire(blocking=False):
        return jsonify({"ok": False, "error": "busy"}), 503

    with STATE_LOCK:
        if update_id in STATE["processed_updates"]:
            UPDATE_SLOTS.release()
            return jsonify({"ok": True})
        # Record delivery before side effects. A duplicate must never create a second log.
        STATE["processed_updates"].append(update_id)
        del STATE["processed_updates"][:-MAX_PROCESSED_UPDATES]
        save_state()

    # Reply to Telegram immediately; do the actual work (which calls Groq/Notion) in the
    # background so Telegram doesn't retry the webhook on a slow LLM/Notion response.
    try:
        threading.Thread(target=handle_update_safely, args=(update,), daemon=True).start()
    except Exception:
        UPDATE_SLOTS.release()
        with STATE_LOCK:
            STATE["processed_updates"].remove(update_id)
            save_state()
        raise
    return jsonify({"ok": True})


def handle_update_safely(update):
    try:
        with STATE_LOCK:
            route_update(update)
            save_state()
    except Exception as e:
        try:
            print(f"Error handling Telegram update: {e}")
            send_message("⚠️ I couldn't process that just now. Please try again in a minute.")
        except Exception:
            pass  # if even sending the error fails, there's nothing more to do
    finally:
        UPDATE_SLOTS.release()


def route_update(update):
    message = update.get("message", {})
    text = message.get("text")
    voice = message.get("voice")

    incoming_text = None
    if voice:
        try:
            audio_bytes = download_voice(voice["file_id"])
            incoming_text = transcribe_audio(audio_bytes)
        except Exception as e:
            send_message(f"⚠️ Couldn't transcribe that voice note: {e}")
            return
    elif text:
        incoming_text = text.strip()

    if not incoming_text:
        return  # sticker, photo, empty message, etc.

    # Commands always go through the existing log flow untouched.
    if incoming_text.startswith("/"):
        STATE["pending"] = poll_log.process_update(update, STATE["pending"], incoming_text=incoming_text)
        return

    lowered = incoming_text.lower().strip(" .!")

    # These exact reserved phrases only ever make sense as continuing/ending a draft that's
    # already in progress — handle them without ever asking the classifier, so "yes"/"cancel"
    # can't accidentally get read as a question.
    reserved_continuation = STATE["pending"] is not None and (
        lowered in poll_log.CONFIRM_WORDS
        or lowered in poll_log.CANCEL_WORDS
        or poll_log.match_new_log_trigger(incoming_text) is not None
    )
    if reserved_continuation:
        STATE["pending"] = poll_log.process_update(update, STATE["pending"], incoming_text=incoming_text)
        return

    # A pending lecture question (poll_log's disambiguation flow) must also capture the very
    # next reply unconditionally. Replies like "D18-P1", "skip", "2", or "Class 5" don't read
    # as a log recap OR a clear question, so classify_intent() tends to default them to QUERY
    # (see its docstring) — which would silently abandon the in-progress lecture flow and send
    # the reply to answer_query() instead, e.g. asking the LLM to make sense of "skip" as a
    # study question. Same fix as reserved_continuation above, one step earlier in the flow.
    if STATE["pending"] is not None and STATE["pending"].get("lecture_pending"):
        STATE["pending"] = poll_log.process_update(update, STATE["pending"], incoming_text=incoming_text)
        return

    # Everything else gets classified fresh, every time — a pending draft does NOT force
    # the next message to be treated as a correction to it. This is the fix for the bug
    # where an empty/misfired draft would silently swallow every message after it,
    # including unrelated questions.
    intent = classify_intent(incoming_text)

    if intent == "log":
        STATE["pending"] = poll_log.process_update(update, STATE["pending"], incoming_text=incoming_text)
        return

    # Conversational query: answer using live plan + logs + lecture tracker data.
    add_to_history("user", incoming_text)
    try:
        reply = answer_query(incoming_text, chat_history=STATE["chat_history"])
    except Exception as e:
        print(f"Error answering Telegram query: {e}")
        reply = "⚠️ I couldn't reach your study data just now. Try asking again in a moment."
    if STATE["pending"] is not None:
        reply += (
            "\n\n_(By the way, you've still got an unsaved log draft from earlier — "
            "reply \"yes\" to save it or \"cancel\" to discard it.)_"
        )
    add_to_history("assistant", reply)
    send_message(reply)


@app.route("/", methods=["GET"])
def health():
    # Handy for Render's health checks, and for confirming the service is actually up.
    return "Study-Bot webhook is running.", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
