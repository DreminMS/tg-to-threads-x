#!/usr/bin/env python3
"""
Publishes the latest Telegram post (album 4620-4624) to TikTok.
Intended to run in GitHub Actions runner (non-RU IP) or locally.
"""

import os
import sys
import json
import sqlite3
import logging
from crosspost import load_config, threads_api_get, init_db
from tiktok_client import post_photos_to_tiktok
from update_status import generate_status

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("publish_latest_to_tiktok")


def publish_latest():
    cfg = load_config()
    token = cfg.get("threads_access_token")

    logger.info("Fetching high-res media URLs from Threads post 18141469717717451...")
    res = threads_api_get("18141469717717451", {"fields": "children{media_url}"}, token)
    urls = [c["media_url"] for c in res.get("children", {}).get("data", [])]

    if not urls:
        logger.error("No media URLs retrieved from Threads post.")
        sys.exit(1)

    logger.info(f"Retrieved {len(urls)} image URLs. Posting photo carousel to TikTok...")
    tt_res = post_photos_to_tiktok(urls, text=None, config=cfg)

    if not tt_res or not tt_res.get("id"):
        logger.error("Failed to post to TikTok: no result returned.")
        sys.exit(1)

    item_id = str(tt_res["id"])
    post_url = tt_res.get("url") or f"https://www.tiktok.com/@dremin_m/photo/{item_id}"
    logger.info(f"Successfully posted to TikTok! ID: {item_id}, URL: {post_url}")

    # Update crosspost.db
    conn = init_db()
    c = conn.cursor()
    c.execute(
        """
        UPDATE processed_posts
        SET tiktok_id = ?, tiktok_url = ?
        WHERE media_group_id = '14340245870664162' OR message_id IN (4620, 4621, 4622, 4623, 4624)
        """,
        (item_id, post_url),
    )
    c.execute(
        """
        UPDATE archive_posts
        SET tiktok_id = ?, tiktok_url = ?
        WHERE tg_message_id IN (4620, 4621, 4622, 4623, 4624)
        """,
        (item_id, post_url),
    )
    conn.commit()
    conn.close()
    logger.info("Updated crosspost.db with TikTok post reference.")

    # Refresh status.json
    generate_status()
    logger.info("Refreshed status.json and docs/status.json.")


if __name__ == "__main__":
    publish_latest()
