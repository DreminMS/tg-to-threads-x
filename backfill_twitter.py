#!/usr/bin/env python3
"""
Backfills missing Twitter posts with a strict 15-minute interval between posts.
Updates SQLite database and synchronizes with GitHub after each publication.
"""

import sys
import os
import time
import json
import sqlite3
import logging
import subprocess
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "backfill_twitter.log")
DB_FILE = os.path.join(BASE_DIR, "crosspost.db")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)
logger = logging.getLogger("backfill_twitter")

POST_IDS = [2938, 2943, 2945]
INTERVAL_SECONDS = 15 * 60  # 15 minutes


def load_config():
    from archive_poster import load_config as _load
    return _load()


def sync_git(post_id: int, tw_id: str):
    try:
        logger.info("Updating status and syncing with GitHub...")
        subprocess.run([sys.executable, "update_status.py"], cwd=BASE_DIR, check=False)
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=BASE_DIR, check=False)
        subprocess.run(["git", "add", "crosspost.db", "status.json", "docs/status.json"], cwd=BASE_DIR, check=False)
        msg = f"Backfill post {post_id} to Twitter ({tw_id}) [skip ci]"
        subprocess.run(["git", "commit", "-m", msg], cwd=BASE_DIR, check=False)
        for attempt in range(3):
            subprocess.run(["git", "pull", "--rebase", "origin", "main"], cwd=BASE_DIR, check=False)
            res = subprocess.run(["git", "push", "origin", "main"], cwd=BASE_DIR, check=False)
            if res.returncode == 0:
                logger.info("✅ Git sync pushed to origin/main.")
                break
            time.sleep(3)
    except Exception as e:
        logger.warning(f"Git sync warning: {e}")


def main():
    from twitter_client import post_photos_to_twitter

    config = load_config()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute(f"""
        SELECT post_id, photos_json, tags, text
        FROM archive_posts
        WHERE post_id IN ({','.join(str(p) for p in POST_IDS)})
          AND (twitter_id IS NULL OR twitter_id = '')
        ORDER BY post_id ASC
    """)
    pending = c.fetchall()
    conn.close()

    total = len(pending)
    logger.info(f"=== Starting Twitter Backfill Queue: {total} posts to publish ===")

    for idx, (post_id, photos_json, tags, text) in enumerate(pending, start=1):
        photos = json.loads(photos_json) if photos_json else []
        caption = (tags or "").strip() or "#streetphotography #фотография"

        msk_now = datetime.now(timezone(timedelta(hours=3))).strftime("%H:%M:%S МСК")
        logger.info(f"[{idx}/{total}] [{msk_now}] Publishing Post #{post_id} to Twitter ({len(photos)} photos)...")
        logger.info(f"    Caption: {caption}")

        tw_id = None
        try:
            tw_id = post_photos_to_twitter(photos, text=caption, config=config)
            logger.info(f"    ✅ Successfully published Post #{post_id} to Twitter! Tweet ID: {tw_id}")

            # Update DB
            conn = sqlite3.connect(DB_FILE)
            with conn:
                conn.execute(
                    "UPDATE archive_posts SET twitter_id = ?, error_message = NULL WHERE post_id = ?",
                    (tw_id, post_id)
                )
            conn.close()

            # Push state
            sync_git(post_id, tw_id)

        except Exception as err:
            logger.error(f"    ❌ Failed to publish Post #{post_id} to Twitter: {err}", exc_info=True)
            conn = sqlite3.connect(DB_FILE)
            with conn:
                conn.execute(
                    "UPDATE archive_posts SET error_message = ? WHERE post_id = ?",
                    (f"Twitter backfill error: {err}", post_id)
                )
            conn.close()

        if idx < total:
            mins = INTERVAL_SECONDS // 60
            next_time = (datetime.now(timezone(timedelta(hours=3))) + timedelta(seconds=INTERVAL_SECONDS)).strftime("%H:%M:%S МСК")
            logger.info(f"⏳ Waiting {mins} minutes before next post. Next post scheduled for {next_time}...")
            # Sleep in intervals with countdown logging
            for remaining in range(INTERVAL_SECONDS, 0, -60):
                time.sleep(min(remaining, 60))
                if remaining > 60 and remaining % 300 == 0:
                    logger.info(f"    ... {remaining // 60} minutes remaining until next post")

    logger.info("🎉 All missing Twitter posts have been processed!")


if __name__ == "__main__":
    main()
