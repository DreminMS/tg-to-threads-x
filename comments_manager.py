#!/usr/bin/env python3
"""
Comments & Replies Manager:
Fetches, tracks, and manages replies from other users across Threads and X (Twitter).
"""

import sys
import os
import json
import time
import sqlite3
import logging
import argparse
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, Any, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
DB_FILE = os.path.join(SCRIPT_DIR, "crosspost.db")
LOG_FILE = os.path.join(SCRIPT_DIR, "comments_manager.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)
logger = logging.getLogger("comments_manager")


def load_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            logger.warning(f"Could not read config.json: {e}")

    if os.environ.get("THREADS_USER_ID"):
        cfg["threads_user_id"] = os.environ["THREADS_USER_ID"]
    if os.environ.get("THREADS_ACCESS_TOKEN"):
        cfg["threads_access_token"] = os.environ["THREADS_ACCESS_TOKEN"]
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg["telegram_bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]

    return cfg


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_replies (
                reply_id TEXT PRIMARY KEY,
                platform TEXT,
                post_id INTEGER,
                author_username TEXT,
                text TEXT,
                permalink TEXT,
                action_taken TEXT DEFAULT 'none',
                created_at TIMESTAMP,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    return conn


def threads_api_get(endpoint: str, params: Dict[str, str], access_token: str) -> Dict[str, Any]:
    query = urllib.parse.urlencode({**params, "access_token": access_token})
    url = f"https://graph.threads.net/v1.0/{endpoint}?{query}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def threads_api_post(endpoint: str, data: Dict[str, str], access_token: str) -> Dict[str, Any]:
    encoded_data = urllib.parse.urlencode({**data, "access_token": access_token}).encode("utf-8")
    url = f"https://graph.threads.net/v1.0/{endpoint}"
    req = urllib.request.Request(url, data=encoded_data, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def sync_threads_replies(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Finds all comments from other users on our published posts and indexes them.
    Read-only sync: never posts replies or modifies comments.
    """
    token = config.get("threads_access_token")
    uid = config.get("threads_user_id")
    if not token or not uid:
        logger.warning("Missing threads_access_token or threads_user_id.")
        return []

    conn = init_db()
    c = conn.cursor()

    # Get posts that have recorded replies in metrics or recent published posts
    c.execute("""
        SELECT DISTINCT p.post_id, p.threads_id
        FROM archive_posts p
        LEFT JOIN post_metrics m ON p.post_id = m.post_id
        WHERE p.status = 'published' AND p.threads_id IS NOT NULL AND (m.threads_replies > 0 OR p.post_id >= 2800)
    """)
    posts = c.fetchall()

    new_replies = []
    for post_id, threads_id in posts:
        try:
            res = threads_api_get(
                f"{threads_id}/replies",
                {"fields": "id,text,timestamp,username,permalink"},
                token
            )
            items = res.get("data", [])
            for item in items:
                reply_id = item.get("id")
                username = item.get("username", "")
                text = item.get("text", "")
                created_at = item.get("timestamp")
                permalink = item.get("permalink", "")

                # Skip comments from the author himself
                if username.lower() == "dremin":
                    continue

                # Check if already processed
                c.execute("SELECT reply_id FROM processed_replies WHERE reply_id = ?", (reply_id,))
                row = c.fetchone()

                if not row:
                    with conn:
                        conn.execute("""
                            INSERT INTO processed_replies (reply_id, platform, post_id, author_username, text, permalink, action_taken, created_at)
                            VALUES (?, 'threads', ?, ?, ?, ?, 'indexed', ?)
                        """, (reply_id, post_id, username, text, permalink, created_at))

                    new_replies.append({
                        "reply_id": reply_id,
                        "post_id": post_id,
                        "username": username,
                        "text": text,
                        "permalink": permalink
                    })
                    logger.info(f"New reply from @{username} on post #{post_id}: '{text}'")
        except Exception as e:
            logger.debug(f"Could not fetch replies for post #{post_id} ({threads_id}): {e}")

    return new_replies


def main():
    parser = argparse.ArgumentParser(description="Comments and Replies Sync")
    parser.add_argument("--sync", action="store_true", help="Scan and sync comments from other users")
    args = parser.parse_args()

    config = load_config()
    replies = sync_threads_replies(config)
    print(f"Total new replies indexed: {len(replies)}")
    for r in replies:
        print(f" - @{r['username']} (post #{r['post_id']}): '{r['text']}'")


if __name__ == "__main__":
    main()
