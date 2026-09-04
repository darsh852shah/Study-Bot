"""One-time setup script: creates a 'Bot Memory' database in your Notion workspace.

Run this once, then copy the printed database ID into your environment variables as
NOTION_MEMORY_DB_ID. After that, the bot will automatically read/write memories.

Usage:
    python setup_memory_db.py

Requires these environment variables (same ones the bot already uses):
    NOTION_API_KEY    — your Notion integration token
    NOTION_PAGE_ID    — the Master Plan page ID (the new DB will be created as a child of it)
"""

import os
import requests

NOTION_API_KEY = os.environ.get("NOTION_API_KEY") or input("Enter your NOTION_API_KEY: ").strip()
NOTION_PAGE_ID = os.environ.get("NOTION_PAGE_ID") or input("Enter your NOTION_PAGE_ID (Master Plan page): ").strip()

if not NOTION_API_KEY or not NOTION_PAGE_ID:
    print("❌ Both NOTION_API_KEY and NOTION_PAGE_ID are required.")
    exit(1)

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Notion-Version": "2022-06-28",
    "Content-Type": "application/json",
}


def create_memory_database():
    payload = {
        "parent": {"page_id": NOTION_PAGE_ID},
        "icon": {"type": "emoji", "emoji": "🧠"},
        "title": [{"type": "text", "text": {"content": "Bot Memory"}}],
        "properties": {
            # Title column — the actual memory text
            "Memory": {"title": {}},
            # What kind of memory: preference, pattern, struggle, goal, insight
            "Category": {
                "select": {
                    "options": [
                        {"name": "preference", "color": "blue"},
                        {"name": "pattern", "color": "purple"},
                        {"name": "struggle", "color": "red"},
                        {"name": "goal", "color": "green"},
                        {"name": "insight", "color": "yellow"},
                    ]
                }
            },
            # Was this inferred by the bot or explicitly stated by the user?
            "Source": {
                "select": {
                    "options": [
                        {"name": "bot-inferred", "color": "gray"},
                        {"name": "user-stated", "color": "blue"},
                    ]
                }
            },
            # When the memory was created
            "Date": {"date": {}},
        },
    }

    r = requests.post("https://api.notion.com/v1/databases", headers=HEADERS, json=payload, timeout=(5, 30))
    r.raise_for_status()
    db = r.json()
    db_id = db["id"]

    print("=" * 60)
    print("✅ Bot Memory database created successfully!")
    print(f"   Notion URL: {db['url']}")
    print()
    print("   Add this to your environment variables:")
    print(f"   NOTION_MEMORY_DB_ID={db_id}")
    print("=" * 60)
    return db_id


if __name__ == "__main__":
    create_memory_database()
