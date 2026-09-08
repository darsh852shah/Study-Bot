"""Always-on webhook server for Study-Bot.

Handles /log commands and log drafts (voice note or free text, with confirmation) via
Telegram's webhook. The morning nudge, midday check-in, and evening report continue to
run separately via GitHub Actions cron — this file only deals with inbound messages.

There is no conversational Q&A here anymore: every non-empty incoming message is treated
as an attempt to log study activity (a fresh log, a correction to a pending draft, a
confirm/cancel, or a lecture-name reply), matching the bot's sole purpose of capturing
logs, not chatting about them.
"""

import os
import threading

from flask import Flask, request, jsonify

from telegram_helper import send_message, download_voice
from stt_helper import transcribe_audio
import poll_log

app = Flask(__name__)

TELEGRAM_CHAT_ID = str(os.environ["TELEGRAM_CHAT_ID"])

# Optional but strongly recommended: set this to a random string, and pass the same value
# as secret_token when you call Telegram's setWebhook. Without it, anyone who finds your
# Render URL could POST fake Telegram updates at your bot.
WEBHOOK_SECRET = os.environ.get("TELEGRAM_WEBHOOK_SECRET")

STATE = {"pending": None}
STATE_LOCK = threading.Lock()  # Telegram can deliver updates in quick succession; keep them serialized


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

    # Everything goes through the log flow — commands, a fresh log, a correction to a
    # pending draft, confirm/cancel, or a reply to a lecture-name question.
    STATE["pending"] = poll_log.process_update(update, STATE["pending"], incoming_text=incoming_text)


@app.route("/", methods=["GET"])
def health():
    # Handy for Render's health checks, and for confirming the service is actually up.
    return "Study-Bot webhook is running.", 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
