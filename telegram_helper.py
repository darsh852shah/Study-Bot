import os
import requests

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_message(text):
    r = requests.post(f"{API_URL}/sendMessage", json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
    })
    r.raise_for_status()
    return r.json()


def get_updates(offset=None):
    params = {"timeout": 0}
    if offset is not None:
        params["offset"] = offset
    r = requests.get(f"{API_URL}/getUpdates", params=params)
    r.raise_for_status()
    return r.json().get("result", [])


def download_voice(file_id):
    """Downloads a Telegram voice note and returns its raw audio bytes (Opus/OGG)."""
    r = requests.get(f"{API_URL}/getFile", params={"file_id": file_id})
    r.raise_for_status()
    file_path = r.json()["result"]["file_path"]
    file_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    audio_r = requests.get(file_url)
    audio_r.raise_for_status()
    return audio_r.content
