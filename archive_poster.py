#!/usr/bin/env python3
"""
Archive Poster: Telegram (@dremin_live) -> Meta Threads (@dremin)
Extracts historical photo posts starting from 2022-01-01 (post ID 2168+)
and publishes them to Threads in chronological order with a 1-hour interval.
Posts are published as pure photos/carousels (NO captions/signatures), as requested.
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
import subprocess
import re
from typing import Optional, Dict, Any, List

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
DB_FILE = os.path.join(SCRIPT_DIR, "crosspost.db")
LOG_FILE = os.path.join(SCRIPT_DIR, "archive_poster.log")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)
logger = logging.getLogger("archive_poster")


def load_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            logger.warning(f"Could not read config.json: {e}")

    # Fallback to Environment Variables (for GitHub Actions, Docker, Cloud)
    if os.environ.get("THREADS_USER_ID"):
        cfg["threads_user_id"] = os.environ["THREADS_USER_ID"]
    if os.environ.get("THREADS_ACCESS_TOKEN"):
        cfg["threads_access_token"] = os.environ["THREADS_ACCESS_TOKEN"]
    if os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg["telegram_bot_token"] = os.environ["TELEGRAM_BOT_TOKEN"]
    if os.environ.get("TWITTER_AUTH_TOKEN") and os.environ.get("TWITTER_CT0"):
        cfg["twitter_enabled"] = True
        cfg["twitter_cookies"] = {
            "auth_token": os.environ["TWITTER_AUTH_TOKEN"],
            "ct0": os.environ["TWITTER_CT0"]
        }
    if os.environ.get("TIKTOK_COOKIES_JSON"):
        try:
            cfg["tiktok_cookies"] = json.loads(os.environ["TIKTOK_COOKIES_JSON"])
            cfg["tiktok_enabled"] = True
        except Exception:
            pass
    if os.environ.get("TIKTOK_SESSION_ID"):
        cfg.setdefault("tiktok_cookies", {})["sessionid"] = os.environ["TIKTOK_SESSION_ID"]
        cfg["tiktok_enabled"] = True

    if not cfg.get("threads_access_token"):
        logger.error("Missing threads_access_token in config or environment variables.")
        sys.exit(1)

    return cfg


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS archive_posts (
                post_id INTEGER PRIMARY KEY,
                post_date TEXT,
                photos_json TEXT,
                status TEXT DEFAULT 'pending',
                threads_id TEXT,
                twitter_id TEXT,
                tiktok_id TEXT,
                threads_url TEXT,
                tiktok_url TEXT,
                published_at TIMESTAMP,
                error_message TEXT,
                text TEXT,
                is_meme INTEGER DEFAULT 0,
                skip_reason TEXT
            )
        """)
        for col, col_type in [("twitter_id", "TEXT"), ("threads_url", "TEXT"), ("tiktok_id", "TEXT"), ("tiktok_url", "TEXT"), ("text", "TEXT"), ("is_meme", "INTEGER DEFAULT 0"), ("skip_reason", "TEXT")]:
            try:
                conn.execute(f"ALTER TABLE archive_posts ADD COLUMN {col} {col_type}")
            except Exception:
                pass
    return conn


# ==========================================
# Telegram Archive Scraper
# ==========================================

def fetch_page_html(url: str, max_retries: int = 3) -> Optional[str]:
    cmd = [
        "curl", "-4", "-s", "-L",
        "--connect-timeout", "10",
        "--max-time", "20",
        "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        url
    ]
    for attempt in range(max_retries):
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            if res.returncode == 0 and res.stdout:
                return res.stdout
            logger.warning(f"Curl returned code {res.returncode} for {url}")
        except Exception as e:
            logger.warning(f"Fetch failed for {url} (attempt {attempt+1}/{max_retries}): {e}")
        time.sleep(1)
    return None


def sync_archive(start_after: int = 2167) -> int:
    """
    Crawls https://t.me/s/dremin_live starting after start_after (2022-01-01)
    and saves all photo posts into archive_posts table.
    """
    conn = init_db()
    cur = conn.cursor()
    cur.execute("SELECT max(post_id) FROM archive_posts")
    max_stored = cur.fetchone()[0]
    if max_stored and max_stored > start_after:
        logger.info(f"Resuming sync from already indexed max post {max_stored}...")
        start_after = max_stored

    current_url = f"https://t.me/s/dremin_live?after={start_after}"
    total_added = 0
    pages_crawled = 0

    logger.info(f"Starting archive sync from post {start_after} (2022-01-01 onwards)...")

    while current_url:
        pages_crawled += 1
        html = fetch_page_html(current_url)
        if not html:
            logger.error(f"Failed to fetch {current_url}, stopping sync.")
            break

        # Parse message blocks
        chunks = html.split('<div class="tgme_widget_message_wrap')
        for chunk in chunks[1:]:
            post_m = re.search(r'data-post="dremin_live/([0-9]+)"', chunk)
            if not post_m:
                continue
            post_id = int(post_m.group(1))
            if post_id <= start_after:
                continue

            # Extract photos (telesco.pe CDN or .jpg, exclude emojis and icons)
            raw_photos = re.findall(r'background-image:url\(\'([^\']+)\'\)', chunk)
            photos = [p for p in raw_photos if ("telesco.pe" in p or p.endswith(".jpg")) and "emoji" not in p]

            if not photos:
                continue

            time_m = re.search(r'<time datetime="([^"]+)"', chunk)
            post_date = time_m.group(1) if time_m else ""

            # Check if already in db
            cur = conn.cursor()
            cur.execute("SELECT 1 FROM archive_posts WHERE post_id = ?", (post_id,))
            if not cur.fetchone():
                # Check if already processed by real-time crossposter
                cur.execute("SELECT threads_id, twitter_id FROM processed_posts WHERE message_id = ?", (post_id,))
                p_row = cur.fetchone()
                if p_row:
                    with conn:
                        conn.execute("""
                            INSERT INTO archive_posts (post_id, post_date, photos_json, status, threads_id, twitter_id, published_at)
                            VALUES (?, ?, ?, 'published', ?, ?, CURRENT_TIMESTAMP)
                        """, (post_id, post_date, json.dumps(photos), p_row[0], p_row[1]))
                else:
                    with conn:
                        conn.execute("""
                            INSERT INTO archive_posts (post_id, post_date, photos_json, status)
                            VALUES (?, ?, ?, 'pending')
                        """, (post_id, post_date, json.dumps(photos)))
                    total_added += 1

        # Check next pagination link
        next_m = re.search(r'<link rel="next" href="([^"]+)"', html)
        if next_m:
            next_path = next_m.group(1)
            # Avoid infinite loop if next_path doesn't change
            new_url = f"https://t.me{next_path}" if next_path.startswith("/") else next_path
            if new_url == current_url:
                break
            current_url = new_url
            time.sleep(0.3)
        else:
            break

    logger.info(f"Sync complete! Crawled {pages_crawled} pages. Added {total_added} new photo posts to archive.")
    return total_added


# ==========================================
# Meta Threads Publishing
# ==========================================

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
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Threads API Error ({e.code}) on {endpoint}: {err_body}")


def threads_api_get(endpoint: str, params: Dict[str, str], access_token: str) -> Dict[str, Any]:
    query_params = dict(params)
    query_params["access_token"] = access_token
    url = f"https://graph.threads.net/v1.0/{endpoint}?{urllib.parse.urlencode(query_params)}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Threads API GET Error ({e.code}) on {endpoint}: {err_body}")


def threads_api_delete(endpoint: str, access_token: str) -> Dict[str, Any]:
    url = f"https://graph.threads.net/v1.0/{endpoint}?access_token={access_token}"
    req = urllib.request.Request(url, method="DELETE")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="ignore")
        if "does not exist" in err_body or e.code == 404:
            logger.info(f"Threads post {endpoint} does not exist or was already deleted.")
            return {"success": True}
        raise RuntimeError(f"Threads API DELETE Error ({e.code}) on {endpoint}: {err_body}")


def delete_threads_post(threads_id: str, access_token: str) -> bool:
    try:
        res = threads_api_delete(threads_id, access_token)
        return bool(res.get("success", True))
    except Exception as e:
        logger.error(f"Failed to delete Threads post {threads_id}: {e}")
        return False



def wait_for_container(creation_id: str, access_token: str, max_retries: int = 15, delay: float = 2.0) -> bool:
    for _ in range(max_retries):
        try:
            info = threads_api_get(creation_id, {"fields": "status,error_message"}, access_token)
            status = info.get("status")
            if status == "FINISHED":
                return True
            if status == "ERROR":
                err = info.get("error_message", "Unknown container error")
                raise RuntimeError(f"Container {creation_id} failed with error: {err}")
        except Exception as e:
            logger.warning(f"Waiting for container {creation_id}... ({e})")
        time.sleep(delay)
    return True


def publish_threads_container(user_id: str, creation_id: str, access_token: str) -> str:
    wait_for_container(creation_id, access_token)
    res = threads_api_post(
        f"{user_id}/threads_publish",
        {"creation_id": creation_id},
        access_token
    )
    post_id = res.get("id")
    if not post_id:
        raise RuntimeError(f"Failed to publish container: {res}")
    return post_id


def refresh_threads_token_if_needed(config: Dict[str, Any]):
    now = time.time()
    last_refresh = config.get("last_token_refresh", 0)
    if now - last_refresh > 30 * 86400:
        token = config.get("threads_access_token")
        if not token:
            return
        try:
            url = f"https://graph.threads.net/refresh_access_token?grant_type=th_refresh_token&access_token={token}"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                new_token = data.get("access_token")
                if new_token:
                    config["threads_access_token"] = new_token
                    config["last_token_refresh"] = int(time.time())
                    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                        json.dump(config, f, indent=2, ensure_ascii=False)
                    logger.info("Threads access token refreshed successfully.")
        except Exception as e:
            logger.warning(f"Failed to refresh token: {e}")


def check_remote_recent_publication(config: Dict[str, Any], min_minutes: int = 40) -> bool:
    """
    Checks remote Threads platform to see if a post was already made in the last min_minutes.
    Returns True if a recent publication exists, meaning posting should be aborted to prevent duplicates.
    """
    from datetime import datetime, timezone
    token = config.get("threads_access_token")
    if token:
        try:
            url = f"https://graph.threads.net/v1.0/me/threads?fields=id,timestamp&access_token={token}&limit=1"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                threads = data.get("data", [])
                if threads:
                    ts_str = threads[0].get("timestamp")
                    if ts_str:
                        dt = datetime.fromisoformat(ts_str.replace("+0000", "+00:00"))
                        diff_mins = (datetime.now(timezone.utc) - dt).total_seconds() / 60
                        if diff_mins < min_minutes:
                            logger.warning(f"DUPLICATE GUARD: Threads post {threads[0]['id']} was published {diff_mins:.1f}m ago (<{min_minutes}m). Aborting to prevent duplicate!")
                            return True
        except Exception as e:
            logger.warning(f"Could not verify remote Threads status: {e}")

    return False


def publish_next_archive_post(config: Dict[str, Any], dry_run: bool = False) -> Optional[int]:
    """
    Fetches the oldest pending post and publishes it to Threads and Twitter without captions.
    Starts posting strictly from 07:00 AM MSK onwards.
    """
    from datetime import datetime, timezone, timedelta
    msk_now = datetime.now(timezone(timedelta(hours=3)))
    if msk_now.hour < 7:
        logger.info(f"Archive posting is scheduled to start at 07:00 MSK (currently {msk_now.strftime('%H:%M')} MSK). Skipping archive post for now.")
        return None

    # Check remote duplicate guard to prevent double-posting
    if check_remote_recent_publication(config, min_minutes=40):
        logger.info("Remote duplicate guard: a post was published < 40m ago. Aborting publication.")
        return None

    refresh_threads_token_if_needed(config)
    conn = init_db()
    cur = conn.cursor()

    # 1. Sync any posts that were already crossposted in real-time
    with conn:
        conn.execute("""
            UPDATE archive_posts
            SET status = 'published',
                threads_id = (SELECT threads_id FROM processed_posts WHERE processed_posts.message_id = archive_posts.post_id),
                twitter_id = (SELECT twitter_id FROM processed_posts WHERE processed_posts.message_id = archive_posts.post_id),
                published_at = CURRENT_TIMESTAMP
            WHERE status = 'pending' AND post_id IN (SELECT message_id FROM processed_posts)
        """)

    # 2. Select oldest pending post that has NOT been processed in real-time
    cur.execute("""
        SELECT a.post_id, a.post_date, a.photos_json
        FROM archive_posts a
        LEFT JOIN processed_posts p ON a.post_id = p.message_id
        WHERE a.status = 'pending' AND p.message_id IS NULL
        ORDER BY a.post_id ASC
        LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        logger.info("No pending archive posts found in queue.")
        return None

    post_id, post_date, photos_json = row
    photos = json.loads(photos_json)

    # 3. Atomically claim the post so no concurrent runner can pick it up
    with conn:
        res = conn.execute("UPDATE archive_posts SET status = 'publishing' WHERE post_id = ? AND status = 'pending'", (post_id,))
        if res.rowcount == 0:
            logger.info(f"Post {post_id} is already claimed by another runner. Skipping.")
            return None

    logger.info(f"Publishing archive post {post_id} ({post_date}) with {len(photos)} photos...")

    if dry_run:
        logger.info(f"[DRY RUN] Would publish post {post_id} with {len(photos)} photos.")
        return post_id

    user_id = config["threads_user_id"]
    access_token = config["threads_access_token"]

    try:
        # Case 1: Carousel (2-10 photos)
        if len(photos) > 1:
            carousel_item_ids = []
            for i, photo_url in enumerate(photos[:10]):
                res = threads_api_post(
                    f"{user_id}/threads",
                    {
                        "media_type": "IMAGE",
                        "image_url": photo_url,
                        "is_carousel_item": "true"
                    },
                    access_token
                )
                item_id = res["id"]
                carousel_item_ids.append(item_id)
                logger.info(f"Carousel item {i+1}/{len(photos)} created: {item_id}")

            for item_id in carousel_item_ids:
                wait_for_container(item_id, access_token)

            # Create carousel container (NO text caption!)
            carousel_data = {
                "media_type": "CAROUSEL",
                "children": ",".join(carousel_item_ids)
            }
            res = threads_api_post(f"{user_id}/threads", carousel_data, access_token)
            creation_id = res["id"]
            threads_id = publish_threads_container(user_id, creation_id, access_token)

        # Case 2: Single Photo
        else:
            post_data = {
                "media_type": "IMAGE",
                "image_url": photos[0]
            }
            res = threads_api_post(f"{user_id}/threads", post_data, access_token)
            creation_id = res["id"]
            threads_id = publish_threads_container(user_id, creation_id, access_token)

        # Twitter Posting (if enabled in config)
        twitter_id = None
        if config.get("twitter_enabled"):
            try:
                from twitter_client import post_photos_to_twitter
                logger.info(f"Publishing post {post_id} to Twitter...")
                twitter_id = post_photos_to_twitter(photos, text=None, config=config)
                logger.info(f"✅ Successfully published post {post_id} to Twitter! Tweet ID: {twitter_id}")
            except Exception as tw_err:
                logger.error(f"❌ Failed to publish post {post_id} to Twitter: {tw_err}")

        # TikTok Posting (if enabled in config)
        tiktok_id = None
        tiktok_url = None
        if config.get("tiktok_enabled"):
            try:
                from tiktok_client import post_photos_to_tiktok
                logger.info(f"Publishing post {post_id} to TikTok...")
                tt_res = post_photos_to_tiktok(photos, text=None, config=config)
                if tt_res:
                    tiktok_id = tt_res.get("id")
                    tiktok_url = tt_res.get("url")
                    logger.info(f"✅ Successfully published post {post_id} to TikTok! ID: {tiktok_id} ({tiktok_url})")
            except Exception as tt_err:
                logger.error(f"❌ Failed to publish post {post_id} to TikTok: {tt_err}")

        threads_url = None
        if threads_id:
            try:
                p_info = threads_api_get(threads_id, {"fields": "permalink"}, access_token)
                threads_url = p_info.get("permalink")
            except Exception:
                threads_url = f"https://www.threads.net/@dremin"

        with conn:
            conn.execute("""
                UPDATE archive_posts
                SET status = 'published', threads_id = ?, twitter_id = ?, tiktok_id = ?, threads_url = ?, tiktok_url = ?, published_at = CURRENT_TIMESTAMP
                WHERE post_id = ?
            """, (threads_id, twitter_id, tiktok_id, threads_url, tiktok_url, post_id))

        logger.info(f"✅ Successfully published post {post_id}! Threads: {threads_id} ({threads_url}), Twitter: {twitter_id}, TikTok: {tiktok_id} ({tiktok_url})")
        return post_id

    except Exception as e:
        logger.error(f"❌ Failed to publish post {post_id}: {e}", exc_info=True)
        with conn:
            conn.execute("""
                UPDATE archive_posts
                SET status = 'failed', error_message = ?
                WHERE post_id = ?
            """, (str(e), post_id))
        return None


def get_queue_status() -> Dict[str, Any]:
    conn = init_db()
    cur = conn.cursor()
    cur.execute("SELECT status, count(*) FROM archive_posts GROUP BY status")
    counts = dict(cur.fetchall())
    cur.execute("SELECT post_id, post_date, photos_json FROM archive_posts WHERE status = 'pending' ORDER BY post_id ASC LIMIT 1")
    next_post = cur.fetchone()
    return {
        "pending": counts.get("pending", 0),
        "published": counts.get("published", 0),
        "failed": counts.get("failed", 0),
        "next_post_id": next_post[0] if next_post else None,
        "next_post_date": next_post[1] if next_post else None,
        "next_post_photos": len(json.loads(next_post[2])) if next_post else 0
    }


def run_daemon(interval_seconds: int = 3600):
    config = load_config()
    logger.info(f"Starting Archive Poster daemon (interval: {interval_seconds}s / {interval_seconds // 60}m)...")
    while True:
        try:
            published_id = publish_next_archive_post(config)
            if not published_id:
                logger.info("No pending posts. Checking again in 10 minutes...")
                time.sleep(600)
                continue
        except Exception as e:
            logger.error(f"Error in daemon cycle: {e}", exc_info=True)

        logger.info(f"Sleeping for {interval_seconds} seconds until next post...")
        time.sleep(interval_seconds)


def skip_archive_post(post_id: int, reason: str = "manual_skip") -> bool:
    conn = init_db()
    with conn:
        cursor = conn.execute("UPDATE archive_posts SET status = 'skipped', skip_reason = ? WHERE post_id = ?", (reason, post_id))
        updated = cursor.rowcount > 0
    if updated:
        logger.info(f"⏭ Post #{post_id} marked as skipped (reason: {reason})")
        try:
            from update_status import update_status_json
            update_status_json()
        except Exception as e:
            logger.warning(f"Failed to update status.json after skip: {e}")
    else:
        logger.warning(f"Post #{post_id} not found in archive_posts")
    return updated


def delete_archive_post(post_id: int, config: Optional[Dict[str, Any]] = None) -> bool:
    """
    Deletes a published post from Threads and Twitter/X, and marks it as deleted in DB.
    """
    if config is None:
        config = load_config()

    conn = init_db()
    cur = conn.cursor()
    cur.execute("SELECT threads_id, twitter_id, tiktok_id, status FROM archive_posts WHERE post_id = ?", (post_id,))
    row = cur.fetchone()

    threads_id = row[0] if row else None
    twitter_id = row[1] if row else None
    tiktok_id = row[2] if row else None

    # Fallback to processed_posts if needed
    cur.execute("SELECT threads_id, twitter_id, tiktok_id FROM processed_posts WHERE message_id = ?", (post_id,))
    p_row = cur.fetchone()
    if p_row:
        threads_id = threads_id or p_row[0]
        twitter_id = twitter_id or p_row[1]
        tiktok_id = tiktok_id or p_row[2]

    if not threads_id and not twitter_id and not tiktok_id and not row:
        logger.error(f"Post #{post_id} not found in database.")
        return False

    logger.info(f"🗑 Initiating deletion of post #{post_id} (Threads: {threads_id}, Twitter: {twitter_id}, TikTok: {tiktok_id})...")

    # 1. Delete from Threads
    threads_ok = True
    if threads_id and config.get("threads_access_token"):
        threads_ok = delete_threads_post(threads_id, config["threads_access_token"])
        if threads_ok:
            logger.info(f"✅ Deleted post #{post_id} from Threads ({threads_id})")
        else:
            logger.warning(f"⚠️ Could not delete post #{post_id} from Threads")

    # 2. Delete from Twitter / X
    twitter_ok = True
    if twitter_id and config.get("twitter_enabled"):
        try:
            from twitter_client import delete_tweet_from_twitter
            twitter_ok = delete_tweet_from_twitter(twitter_id, config)
            if twitter_ok:
                logger.info(f"✅ Deleted post #{post_id} from Twitter ({twitter_id})")
            else:
                logger.warning(f"⚠️ Could not delete post #{post_id} from Twitter")
        except Exception as tw_err:
            logger.error(f"❌ Error deleting tweet {twitter_id}: {tw_err}")
            twitter_ok = False

    # 3. Delete from TikTok
    tiktok_ok = True
    if tiktok_id and config.get("tiktok_enabled"):
        try:
            from tiktok_client import delete_post_from_tiktok
            tiktok_ok = delete_post_from_tiktok(tiktok_id, config)
            if tiktok_ok:
                logger.info(f"✅ Deleted post #{post_id} from TikTok ({tiktok_id})")
            else:
                logger.warning(f"⚠️ Could not delete post #{post_id} from TikTok")
        except Exception as tt_err:
            logger.error(f"❌ Error deleting TikTok post {tiktok_id}: {tt_err}")
            tiktok_ok = False

    # 4. Update database records
    with conn:
        conn.execute("""
            UPDATE archive_posts 
            SET status = 'deleted', error_message = 'Deleted by user via PWA'
            WHERE post_id = ?
        """, (post_id,))
        conn.execute("DELETE FROM processed_posts WHERE message_id = ?", (post_id,))

    logger.info(f"🗑 Post #{post_id} marked as 'deleted' in database.")

    try:
        from update_status import update_status_json
        update_status_json()
    except Exception as e:
        logger.warning(f"Could not update status.json after deletion: {e}")

    return True



def publish_hourly(config: Dict[str, Any], dry_run: bool = False) -> Optional[int]:
    """
    Checks if at least 50 minutes have elapsed since the last published archive post and current hour >= 7 MSK.
    If so, publishes the next archive post.
    """
    from datetime import datetime, timezone, timedelta
    msk_now = datetime.now(timezone(timedelta(hours=3)))
    if msk_now.hour < 7:
        logger.info(f"Archive posting is scheduled from 07:00 to 00:00 MSK (currently {msk_now.strftime('%H:%M')} MSK). Waiting.")
        return None

    conn = init_db()
    cur = conn.cursor()
    cur.execute("SELECT max(published_at) FROM archive_posts WHERE status = 'published'")
    row = cur.fetchone()
    last_pub = row[0] if row else None
    if last_pub:
        try:
            dt = datetime.fromisoformat(last_pub)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            diff_mins = (now - dt).total_seconds() / 60
            if diff_mins < 50:
                logger.info(f"Last archive post was published {diff_mins:.1f}m ago (<50m). Waiting for next hour slot.")
                return None
        except Exception as e:
            logger.warning(f"Could not parse last_pub '{last_pub}': {e}")

    logger.info("Hourly archive slot available! Proceeding with publication...")
    return publish_next_archive_post(config, dry_run=dry_run)


def main():
    parser = argparse.ArgumentParser(description="Archive Poster for Telegram to Threads")
    parser.add_argument("--sync", action="store_true", help="Sync archive from Telegram channel")
    parser.add_argument("--status", action="store_true", help="Show current queue status")
    parser.add_argument("--publish-one", action="store_true", help="Publish 1 post right now")
    parser.add_argument("--publish-hourly", action="store_true", help="Publish 1 post if hourly interval reached")
    parser.add_argument("--skip", type=int, help="Skip a specific post ID")
    parser.add_argument("--skip-reason", type=str, default="manual_skip", help="Reason for skipping")
    parser.add_argument("--delete", type=int, help="Delete a published post by ID from Threads, Twitter, and database")
    parser.add_argument("--dry-run", action="store_true", help="Simulate publishing without posting to Threads")
    parser.add_argument("--daemon", action="store_true", help="Run continuously with 1-hour interval")
    parser.add_argument("--interval", type=int, default=3600, help="Interval in seconds (default: 3600)")
    args = parser.parse_args()

    if args.delete:
        config = load_config()
        delete_archive_post(args.delete, config=config)
        return

    if args.skip:
        skip_archive_post(args.skip, reason=args.skip_reason)
        return

    if args.sync:
        sync_archive()
        return

    if args.status:
        st = get_queue_status()
        print("\n📊 Текущий статус очереди архива:")
        print(f"  Ожидают публикации: {st['pending']}")
        print(f"  Опубликовано:        {st['published']}")
        print(f"  Ошибок:              {st['failed']}")
        if st['next_post_id']:
            print(f"  Следующий пост:      dremin_live/{st['next_post_id']} (дата: {st['next_post_date']}, фото: {st['next_post_photos']})\n")
        else:
            print("  Следующий пост:      нет (очередь пуста)\n")
        return

    config = load_config()

    if args.publish_hourly:
        publish_hourly(config, dry_run=args.dry_run)
        return

    if args.publish_one:
        publish_next_archive_post(config, dry_run=args.dry_run)
        return

    if args.daemon:
        run_daemon(interval_seconds=args.interval)
        return

    parser.print_help()


if __name__ == "__main__":
    main()
