import os
import requests

GROQ_API_KEY = os.environ["GROQ_API_KEY"]
GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
STT_MODEL = "whisper-large-v3-turbo"  # swap to "distil-whisper-large-v3-en" for an English-only, slightly faster option


def transcribe_audio(audio_bytes, filename="voice.ogg"):
    """Sends a voice note's raw bytes to Groq's hosted Whisper and returns the transcript text."""
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    files = {"file": (filename, audio_bytes)}
    data = {"model": STT_MODEL, "response_format": "text"}
    r = requests.post(GROQ_TRANSCRIBE_URL, headers=headers, files=files, data=data, timeout=60)
    r.raise_for_status()
    return r.text.strip()
