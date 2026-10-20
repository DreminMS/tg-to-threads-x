#!/usr/bin/env python3
import os
import sys
import json
import sqlite3
import logging
from crosspost import load_config, post_to_threads, mark_post_processed
from twitter_client import post_photos_to_twitter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("publish_4615")

def main():
    config = load_config()

    # Raw GitHub URLs for Threads carousel (accessible publicly without VPN)
    threads_urls = [
        f"https://raw.githubusercontent.com/DreminMS/tg-to-threads-x/main/media/post_4615/photo_{i}.jpg"
        for i in range(10)
    ]

    # Local file paths for Twitter Playwright file upload
    twitter_files = [
        os.path.abspath(f"media/post_4615/photo_{i}.jpg")
        for i in range(10)
    ]

    for p in twitter_files:
        if not os.path.exists(p):
            logger.error(f"Missing local file: {p}")
            sys.exit(1)

    logger.info("=== 1. Publishing Post 4615 high-res carousel (10 photos) to Threads ===")
    threads_id = post_to_threads(config, text="", media_urls=threads_urls)
    logger.info(f"✅ Published to Threads! ID: {threads_id}")

    logger.info("=== 2. Publishing Post 4615 high-res thread (10 photos: 4+4+2) to Twitter ===")
    twitter_id = post_photos_to_twitter(twitter_files, text=None, config=config)
    logger.info(f"✅ Published to Twitter! Tweet ID: {twitter_id}")

    logger.info("=== 3. Updating Database and Status ===")
    conn = sqlite3.connect("crosspost.db")
    for mid in range(4615, 4625):
        mark_post_processed(conn, mid, "14340245870664162", threads_id, twitter_id)

    with conn:
        conn.execute("""
            UPDATE archive_posts
            SET status = 'published',
                threads_id = ?,
                twitter_id = ?,
                photos_json = ?,
                published_at = CURRENT_TIMESTAMP
            WHERE post_id = 4615
        """, (threads_id, twitter_id, json.dumps(threads_urls)))

    logger.info("✅ All done! Post 4615 published in high quality on both platforms.")

if __name__ == "__main__":
    main()
