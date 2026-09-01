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

State (pending log draft + a little chat memory for conversational continuity) is kept
in-memory per process. That's fine for a single-user bot on an always-on host; it just
means a restart clears any in-progress draft/conversation, same as before.
"""

import os
import threading

from flask import Flask, request, jsonify

from telegram_helper import send_message, download_voice
from llm_helper import classify_intent
from stt_helper import transcribe_audio
import poll_log
from query_handler import answer_query

app = Flask(__name__)

TELEGRAM_CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])
# Optional but strongly recommended: set this to a random string, and pass the same value
# as secret_token when you call Telegram's setWebhook (see SETUP_GUIDE_WEBHOOK.md). Without
# it, anyone who finds your Render URL could POST fake Telegram updates at your bot.
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET")

MAX_HISTORY = 6  # (role, text) turns kept for conversational continuity

STATE = {"pending": None, "chat_history": []}
STATE_LOCK = threading.Lock()  # Telegram can deliver updates in quick succession; keep them serialized


def add_to_history(role, text):
    STATE["chat_history"].append((role, text))
    del STATE["chat_history"][:-MAX_HISTORY]


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():
    if WEBHOOK_SECRET and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
        return jsonify({"ok": False, "error": "bad secret token"}), 403

    update = request.get_json(silent=True) or {}
    message = update.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))

    # This endpoint is a public URL — only ever act on messages from the owner's chat.
    if chat_id and chat_id != TELEGRAM_CHAT_ID:
        return jsonify({"ok": True})

    # Reply to Telegram immediately; do the actual work (which calls Groq/Notion) in the
    # background so Telegram doesn't retry the webhook on a slow LLM/Notion response.
    threading.Thread(target=handle_update_safely, args=(update,), daemon=True).start()
    return jsonify({"ok": True})


def handle_update_safely(update):
    try:
        with STATE_LOCK:
            route_update(update)
    except Exception as e:
        try:
            send_message(f"⚠️ Something broke handling that: {e}")
        except Exception:
            pass  # if even sending the error fails, there's nothing more to do


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

    # Commands, and any message while a log draft is mid-confirmation, always go through
    # the existing log flow untouched — never reclassified as a conversational query.
    if incoming_text.startswith("/") or STATE["pending"] is not None:
        STATE["pending"] = poll_log.process_update(update, STATE["pending"], incoming_text=incoming_text)
        return

    intent = classify_intent(incoming_text)

    if intent == "log":
        STATE["pending"] = poll_log.process_update(update, STATE["pending"], incoming_text=incoming_text)
        return

    # Conversational query: answer using live plan + logs + lecture tracker data.
    add_to_history("user", incoming_text)
    try:
        reply = answer_query(incoming_text, chat_history=STATE["chat_history"])
    except Exception as e:
        reply = f"⚠️ Couldn't work that out just now ({e}). Try asking again in a moment."
    add_to_history("assistant", reply)
    send_message(reply)


@app.route("/", methods=["GET"])
def health():
    # Handy for Render's health checks, and for confirming the service is actually up.
    return "Study-Bot webhook is running.", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
