#!/usr/bin/env python3
"""
Adds topic hashtags as comments (replies) to all published Threads posts.
Processes sequentially with rate-limit pacing and automatic database progress saving.
"""

import sys
import os
import json
import time
import logging
import sqlite3
import urllib.request
import urllib.parse
import urllib.error
from typing import Dict, Any, List, Tuple

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("add_tags_comments")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
DB_PATH = os.path.join(BASE_DIR, "crosspost.db")


def load_config() -> Dict[str, Any]:
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def threads_api_post(endpoint: str, data: Dict[str, str], access_token: str) -> Dict[str, Any]:
    url = f"https://graph.threads.net/v1.0/{endpoint}"
    data_with_token = dict(data)
    data_with_token["access_token"] = access_token
    encoded_data = urllib.parse.urlencode(data_with_token).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=encoded_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def post_reply_with_retry(user_id: str, threads_id: str, tags: str, access_token: str, max_retries: int = 3) -> str:
    """
    Creates a text container replying to threads_id, waits, and publishes it.
    Handles rate limits (HTTP 429) and transient errors with backoff.
    """
    for attempt in range(1, max_retries + 1):
        try:
            # 1. Create container
            create_res = threads_api_post(
                f"{user_id}/threads",
                {
                    "media_type": "TEXT",
                    "text": tags,
                    "reply_to_id": threads_id,
                },
                access_token
            )
            container_id = create_res.get("id")
            if not container_id:
                raise RuntimeError(f"No container ID returned: {create_res}")

            time.sleep(2.0)

            # 2. Publish container
            publish_res = threads_api_post(
                f"{user_id}/threads_publish",
                {"creation_id": container_id},
                access_token
            )
            reply_id = publish_res.get("id")
            if not reply_id:
                raise RuntimeError(f"No publish ID returned: {publish_res}")

            return reply_id

        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            logger.warning(f"Attempt {attempt}/{max_retries} HTTP {e.code} for post {threads_id}: {err_body}")

            # If post was deleted or invalid object, don't keep retrying
            if e.code in (400, 404) and ("not exist" in err_body or "OAuthException" in err_body):
                if "does not exist" in err_body or "Invalid" in err_body:
                    logger.error(f"Post {threads_id} appears deleted or inaccessible on Threads: {err_body}")
                    return "not_found"

            if attempt < max_retries:
                wait_time = 35 if e.code == 429 else 10
                logger.info(f"Sleeping {wait_time}s before retry...")
                time.sleep(wait_time)
            else:
                raise RuntimeError(f"Failed after {max_retries} attempts: {err_body}")

        except Exception as e:
            logger.warning(f"Attempt {attempt}/{max_retries} exception for post {threads_id}: {e}")
            if attempt < max_retries:
                time.sleep(10)
            else:
                raise


def get_pending_posts(conn: sqlite3.Connection) -> List[Tuple[str, str, List[int]]]:
    """
    Returns list of (threads_id, tags, [post_ids]) for published posts without tag replies.
    """
    cur = conn.cursor()
    cur.execute("""
        SELECT post_id, threads_id, tags
        FROM archive_posts
        WHERE status = 'published'
          AND threads_id IS NOT NULL
          AND (tag_comment_threads_id IS NULL OR tag_comment_threads_id = '')
        ORDER BY post_id DESC
    """)
    rows = cur.fetchall()

    grouped: Dict[str, Dict[str, Any]] = {}
    for post_id, threads_id, tags in rows:
        if threads_id not in grouped:
            grouped[threads_id] = {
                "tags": tags or "#фотография",
                "post_ids": []
            }
        grouped[threads_id]["post_ids"].append(post_id)

    return [(tid, data["tags"], data["post_ids"]) for tid, data in grouped.items()]


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Add topic hashtags as replies to published Threads posts")
    parser.add_argument("--limit", type=int, default=0, help="Max posts to comment (0 for all)")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without calling Threads API")
    parser.add_argument("--delay", type=float, default=3.5, help="Delay in seconds between posts (default: 3.5s)")
    args = parser.parse_args()

    cfg = load_config()
    user_id = cfg.get("threads_user_id")
    token = cfg.get("threads_access_token")

    if not user_id or not token:
        logger.error("Missing threads_user_id or threads_access_token in config.json")
        sys.exit(1)

    conn = sqlite3.connect(DB_PATH)
    # Ensure column exists
    try:
        conn.cursor().execute("ALTER TABLE archive_posts ADD COLUMN tag_comment_threads_id TEXT")
        conn.commit()
    except Exception:
        pass

    pending = get_pending_posts(conn)
    total_pending = len(pending)
    logger.info(f"Found {total_pending} unique published Threads posts needing tag comments.")

    if args.limit > 0:
        pending = pending[:args.limit]
        logger.info(f"Limiting to first {len(pending)} posts as requested.")

    if not pending:
        logger.info("🎉 All published posts already have tag comments!")
        return

    success_count = 0
    fail_count = 0

    for idx, (threads_id, tags, post_ids) in enumerate(pending, 1):
        logger.info(f"[{idx}/{len(pending)}] Processing Threads {threads_id} (post_ids: {post_ids}): {tags}")

        if args.dry_run:
            logger.info(f"  [DRY-RUN] Would reply to {threads_id} with: {tags}")
            success_count += 1
            continue

        try:
            reply_id = post_reply_with_retry(user_id, threads_id, tags, token)
            cur = conn.cursor()
            cur.execute(
                "UPDATE archive_posts SET tag_comment_threads_id = ? WHERE threads_id = ?",
                (reply_id, threads_id)
            )
            conn.commit()
            logger.info(f"  ✅ Reply published! ID: {reply_id}")
            success_count += 1
        except Exception as e:
            logger.error(f"  ❌ Failed to reply to {threads_id}: {e}")
            fail_count += 1

        if idx < len(pending):
            time.sleep(args.delay)

    conn.close()
    logger.info(f"\n==========================================")
    logger.info(f"Finished! Successfully commented: {success_count}, Failed: {fail_count}")
    logger.info(f"==========================================")


if __name__ == "__main__":
    main()
