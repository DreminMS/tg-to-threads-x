#!/usr/bin/env python3
"""
Telegram to Threads Auto-Crossposter
Automates publishing posts from Telegram channel (@dremin_live) to Meta Threads
using the official Meta Threads API.
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
from typing import Optional, Dict, Any, List

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")
DB_FILE = os.path.join(SCRIPT_DIR, "crosspost.db")
LOG_FILE = os.path.join(SCRIPT_DIR, "crosspost.log")

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_FILE, encoding="utf-8")
    ]
)
logger = logging.getLogger("crosspost")


def load_config() -> Dict[str, Any]:
    cfg: Dict[str, Any] = {
        "telegram_channel_id": "@dremin_live",
        "append_signature": True,
        "signature_text": "\n\nDremin live | t.me/dremin_live",
        "poll_interval_seconds": 20,
        "threads_enabled": True,
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
                cfg.update(loaded)
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

    if not cfg.get("threads_user_id") or not cfg.get("threads_access_token"):
        logger.error("Missing threads_user_id or threads_access_token in config and environment!")
        sys.exit(1)
    return cfg


def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE)
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_posts (
                message_id INTEGER PRIMARY KEY,
                media_group_id TEXT,
                threads_id TEXT,
                twitter_id TEXT,
                tiktok_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        for col in ["twitter_id", "tiktok_id", "tiktok_url"]:
            try:
                conn.execute(f"ALTER TABLE processed_posts ADD COLUMN {col} TEXT")
            except Exception:
                pass
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bot_state (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
    return conn


def get_stored_offset(conn: sqlite3.Connection) -> int:
    cur = conn.cursor()
    cur.execute("SELECT value FROM bot_state WHERE key = 'last_update_id'")
    row = cur.fetchone()
    return int(row[0]) if row else 0


def set_stored_offset(conn: sqlite3.Connection, offset: int):
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO bot_state (key, value) VALUES ('last_update_id', ?)",
            (str(offset),)
        )


def is_post_processed(conn: sqlite3.Connection, message_id: int) -> bool:
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM processed_posts WHERE message_id = ?", (message_id,))
    if cur.fetchone():
        return True
    cur.execute("SELECT 1 FROM archive_posts WHERE post_id = ? AND status = 'published'", (message_id,))
    return cur.fetchone() is not None


def mark_post_processed(
    conn: sqlite3.Connection,
    message_id: int,
    media_group_id: Optional[str],
    threads_id: Optional[str],
    twitter_id: Optional[str] = None,
    tiktok_id: Optional[str] = None
):
    with conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO processed_posts (message_id, media_group_id, threads_id, twitter_id, tiktok_id)
            VALUES (?, ?, ?, ?, ?)
            """,
            (message_id, media_group_id, threads_id, twitter_id, tiktok_id)
        )
        # Crucial: mark as published in archive_posts too, so archive poster NEVER publishes it as an archive post
        conn.execute(
            """
            INSERT INTO archive_posts (post_id, post_date, photos_json, status, threads_id, twitter_id, tiktok_id, published_at)
            VALUES (?, CURRENT_TIMESTAMP, '[]', 'published', ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(post_id) DO UPDATE SET
                status = 'published',
                threads_id = COALESCE(excluded.threads_id, archive_posts.threads_id),
                twitter_id = COALESCE(excluded.twitter_id, archive_posts.twitter_id),
                tiktok_id = COALESCE(excluded.tiktok_id, archive_posts.tiktok_id),
                published_at = CURRENT_TIMESTAMP
            """,
            (message_id, threads_id, twitter_id, tiktok_id)
        )


# ==========================================
# Telegram API Helpers
# ==========================================

def tg_request(bot_token: str, method: str, params: Optional[Dict[str, Any]] = None, max_retries: int = 4) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{bot_token}/{method}"
    data = None
    if params:
        data = json.dumps(params).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {}
    )
    last_err = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                res_data = json.loads(resp.read().decode("utf-8"))
                if not res_data.get("ok"):
                    raise RuntimeError(f"Telegram API Error: {res_data}")
                return res_data["result"]
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            last_err = RuntimeError(f"HTTPError {e.code} on {method}: {err_body}")
        except Exception as e:
            last_err = e

        if attempt < max_retries - 1:
            time.sleep(2.0 * (attempt + 1))

    raise RuntimeError(f"Telegram API request failed after {max_retries} attempts for {method}: {last_err}")


def get_telegram_file_url(bot_token: str, file_id: str) -> str:
    file_info = tg_request(bot_token, "getFile", {"file_id": file_id})
    file_path = file_info["file_path"]
    return f"https://api.telegram.org/file/bot{bot_token}/{file_path}"



# ==========================================
# Meta Threads API Helpers
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


def refresh_threads_token(config: Dict[str, Any]) -> bool:
    """
    Refreshes the long-lived Threads access token and saves it back to config.json.
    """
    token = config.get("threads_access_token")
    if not token:
        return False
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
                logger.info(f"Threads access token refreshed successfully (expires in {data.get('expires_in')}s).")
                return True
    except Exception as e:
        logger.warning(f"Failed to refresh Threads access token: {e}")
    return False



def wait_for_container(creation_id: str, access_token: str, max_retries: int = 15, delay: float = 2.0) -> bool:
    """Polls container status until it becomes FINISHED or fails."""
    for attempt in range(max_retries):
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


def post_to_threads(config: Dict[str, Any], text: str, media_urls: List[str]) -> str:
    """
    Publishes text, a single image, or a carousel of up to 10 images to Threads.
    """
    user_id = config["threads_user_id"]
    access_token = config["threads_access_token"]

    if config.get("append_signature") and config.get("signature_text"):
        if text:
            text = text.strip() + config["signature_text"]
        else:
            text = config["signature_text"].strip()

    # Case 1: Carousel (2 to 10 images)
    if len(media_urls) > 1:
        logger.info(f"Creating carousel with {len(media_urls)} items...")
        carousel_item_ids = []
        for i, url in enumerate(media_urls[:10]):
            item_id = None
            for attempt in range(4):
                try:
                    res = threads_api_post(
                        f"{user_id}/threads",
                        {
                            "media_type": "IMAGE",
                            "image_url": url,
                            "is_carousel_item": "true"
                        },
                        access_token
                    )
                    item_id = res.get("id")
                    if item_id:
                        break
                except Exception as e:
                    if attempt == 3:
                        raise
                    logger.warning(f"Carousel item {i+1} attempt {attempt+1} failed ({e}), retrying in {(attempt+1)*2}s...")
                    time.sleep(2.0 * (attempt + 1))

            carousel_item_ids.append(item_id)
            logger.info(f"Carousel item {i+1}/{len(media_urls)} created: {item_id}")

        # Wait for all items to be processed
        for item_id in carousel_item_ids:
            wait_for_container(item_id, access_token)

        # Create Carousel Container
        carousel_data = {
            "media_type": "CAROUSEL",
            "children": ",".join(carousel_item_ids)
        }
        if text:
            carousel_data["text"] = text

        res = threads_api_post(f"{user_id}/threads", carousel_data, access_token)
        creation_id = res["id"]
        logger.info(f"Carousel container created: {creation_id}")
        return publish_threads_container(user_id, creation_id, access_token)

    # Case 2: Single Image
    elif len(media_urls) == 1:
        logger.info(f"Creating single image post...")
        post_data = {
            "media_type": "IMAGE",
            "image_url": media_urls[0]
        }
        if text:
            post_data["text"] = text

        res = threads_api_post(f"{user_id}/threads", post_data, access_token)
        creation_id = res["id"]
        logger.info(f"Image container created: {creation_id}")
        return publish_threads_container(user_id, creation_id, access_token)

    # Case 3: Text Only
    else:
        logger.info(f"Creating text post...")
        res = threads_api_post(
            f"{user_id}/threads",
            {"media_type": "TEXT", "text": text},
            access_token
        )
        creation_id = res["id"]
        logger.info(f"Text container created: {creation_id}")
        return publish_threads_container(user_id, creation_id, access_token)


# ==========================================
# Main Processing & Polling Loop
# ==========================================

class MediaGroupBuffer:
    def __init__(self, group_id: str, first_seen: float):
        self.group_id = group_id
        self.first_seen = first_seen
        self.messages: List[Dict[str, Any]] = []

    def add_message(self, msg: Dict[str, Any]):
        self.messages.append(msg)

    def is_ready(self, wait_seconds: float = 3.0) -> bool:
        return (time.time() - self.first_seen) >= wait_seconds


def process_channel_post(config: Dict[str, Any], messages: List[Dict[str, Any]], dry_run: bool = False) -> Optional[str]:
    """
    Extracts text and media URLs from a message or group of messages and posts to Threads.
    """
    bot_token = config["telegram_bot_token"]
    text = ""
    media_urls = []

    for msg in messages:
        # Check text or caption
        if not text:
            text = msg.get("caption") or msg.get("text") or ""

        # Extract photo (highest resolution is the last item)
        if "photo" in msg and msg["photo"]:
            best_photo = msg["photo"][-1]
            file_id = best_photo["file_id"]
            url = get_telegram_file_url(bot_token, file_id)
            media_urls.append(url)

    if not text and not media_urls:
        logger.info("Message contains no supported text or photo, skipping.")
        return None

    logger.info(f"Prepared post: text='{text[:60]}...', {len(media_urls)} photos")

    if dry_run:
        logger.info("[DRY RUN] Would publish to Threads and Twitter.")
        return ("dry_run_threads", "dry_run_twitter")

    threads_id = None
    if config.get("threads_enabled", True):
        try:
            threads_id = post_to_threads(config, text, media_urls)
            logger.info(f"Published to Threads: {threads_id}")
        except Exception as e:
            logger.error(f"Failed to post to Threads: {e}")

    twitter_id = None
    if config.get("twitter_enabled"):
        try:
            from twitter_client import post_photos_to_twitter, post_tweet
            tw_text = text
            if config.get("append_signature") and config.get("signature_text"):
                tw_text = (text.strip() + config["signature_text"]).strip() if text else config["signature_text"].strip()
            if media_urls:
                twitter_id = post_photos_to_twitter(media_urls, text=tw_text, config=config)
            elif tw_text:
                res = post_tweet(text=tw_text, media_ids=None, config=config)
                twitter_id = res.get("data", {}).get("id")
            logger.info(f"Published to Twitter: {twitter_id}")
        except Exception as tw_err:
            logger.error(f"Failed to post to Twitter: {tw_err}")

    tiktok_id = None
    if config.get("tiktok_enabled") and media_urls:
        try:
            from tiktok_client import post_photos_to_tiktok
            tt_res = post_photos_to_tiktok(media_urls, text=text, config=config)
            if tt_res:
                tiktok_id = tt_res.get("id")
                logger.info(f"Published to TikTok: {tiktok_id}")
        except Exception as tt_err:
            logger.error(f"Failed to post to TikTok: {tt_err}")

    return (threads_id, twitter_id, tiktok_id)


def run_poller(config: Dict[str, Any], dry_run: bool = False, once: bool = False) -> int:
    conn = init_db()
    bot_token = config["telegram_bot_token"]
    target_channel = str(config.get("telegram_channel_id", "")).strip().lower()

    offset = get_stored_offset(conn)
    logger.info(f"Checking Telegram updates for channel {target_channel} (offset: {offset}, once={once})...")

    media_groups: Dict[str, MediaGroupBuffer] = {}
    last_refresh_check = 0.0
    published_count = 0

    while True:
        try:
            from archive_poster import is_posting_paused
            if is_posting_paused(conn):
                logger.info("⏸️ Crossposting is PAUSED by user. Waiting.")
                if once:
                    return 0
                time.sleep(poll_interval)
                continue

            # Check token refresh periodically (if older than 30 days)
            now = time.time()
            if now - last_refresh_check > 86400:
                last_refresh_check = now
                if now - config.get("last_token_refresh", 0) > 30 * 86400:
                    refresh_threads_token(config)

            # 1. Check for ready media groups
            ready_groups = [gid for gid, buf in media_groups.items() if buf.is_ready(wait_seconds=0.0 if once else 3.0)]
            for gid in ready_groups:
                buf = media_groups[gid]
                msg_ids = [m["message_id"] for m in buf.messages]
                logger.info(f"Processing media group {gid} (messages: {msg_ids})")

                try:
                    threads_id, twitter_id, tiktok_id = process_channel_post(config, buf.messages, dry_run=dry_run)
                    for mid in msg_ids:
                        mark_post_processed(conn, mid, gid, threads_id, twitter_id, tiktok_id)
                    logger.info(f"Media group {gid} published with Threads: {threads_id}, Twitter: {twitter_id}, TikTok: {tiktok_id}")
                    published_count += 1
                    media_groups.pop(gid, None)
                except Exception as err:
                    logger.error(f"Error publishing media group {gid}: {err}", exc_info=True)
                    if once:
                        raise
                    buf.retries = getattr(buf, "retries", 0) + 1
                    if buf.retries > 5:
                        logger.error(f"Media group {gid} exceeded max retries. Dropping.")
                        media_groups.pop(gid, None)
                    else:
                        buf.first_seen = time.time() + 10  # retry in 10 seconds

            # 2. Fetch Telegram updates
            timeout_sec = 2 if once else 15
            updates = tg_request(bot_token, "getUpdates", {
                "offset": offset,
                "timeout": timeout_sec,
                "allowed_updates": ["channel_post"]
            })

            if not updates and once and not media_groups:
                logger.info(f"Finished one-shot check. New posts published: {published_count}.")
                return published_count

            for u in updates:
                update_id = u["update_id"]
                offset = max(offset, update_id + 1)
                set_stored_offset(conn, offset)

                post = u.get("channel_post")
                if not post:
                    continue

                chat = post.get("chat", {})
                chat_username = f"@{chat.get('username', '')}".lower()
                chat_id = str(chat.get("id"))

                # Verify channel match
                if target_channel and (target_channel != chat_username and target_channel != chat_id):
                    logger.debug(f"Ignoring post from chat {chat_username} ({chat_id})")
                    continue

                msg_id = post["message_id"]
                if is_post_processed(conn, msg_id):
                    logger.debug(f"Message {msg_id} already processed, skipping.")
                    continue

                media_group_id = post.get("media_group_id")
                if media_group_id:
                    if media_group_id not in media_groups:
                        media_groups[media_group_id] = MediaGroupBuffer(media_group_id, time.time())
                    media_groups[media_group_id].add_message(post)
                    logger.info(f"Buffered item for media group {media_group_id} (msg_id: {msg_id})")
                else:
                    logger.info(f"Processing standalone message {msg_id}")
                    threads_id, twitter_id, tiktok_id = process_channel_post(config, [post], dry_run=dry_run)
                    mark_post_processed(conn, msg_id, None, threads_id, twitter_id, tiktok_id)
                    logger.info(f"Message {msg_id} published with Threads: {threads_id}, Twitter: {twitter_id}, TikTok: {tiktok_id}")
                    published_count += 1

            if once:
                # Flush and process any media groups from the received batch
                for gid, buf in list(media_groups.items()):
                    msg_ids = [m["message_id"] for m in buf.messages]
                    logger.info(f"Processing media group {gid} (messages: {msg_ids})")
                    threads_id, twitter_id, tiktok_id = process_channel_post(config, buf.messages, dry_run=dry_run)
                    for mid in msg_ids:
                        mark_post_processed(conn, mid, gid, threads_id, twitter_id, tiktok_id)
                    logger.info(f"Media group {gid} published with Threads: {threads_id}, Twitter: {twitter_id}, TikTok: {tiktok_id}")
                    published_count += 1
                    media_groups.pop(gid, None)

                logger.info(f"Finished one-shot check. New posts published: {published_count}.")
                return published_count

        except Exception as e:
            logger.error(f"Error in poller cycle: {e}", exc_info=True)
            if once:
                raise
            time.sleep(5)

        time.sleep(1)


# ==========================================
# CLI Commands
# ==========================================

def test_telegram(config: Dict[str, Any]):
    token = config.get("telegram_bot_token")
    if not token or token == "YOUR_TELEGRAM_BOT_TOKEN":
        print("❌ telegram_bot_token is not configured.")
        return
    try:
        me = tg_request(token, "getMe")
        print(f"✅ Telegram Bot connected: @{me.get('username')} ({me.get('first_name')})")
    except Exception as e:
        print(f"❌ Telegram connection failed: {e}")


def test_threads(config: Dict[str, Any]):
    user_id = config.get("threads_user_id")
    token = config.get("threads_access_token")
    if not user_id or user_id == "YOUR_THREADS_USER_ID" or not token:
        print("❌ threads_user_id or threads_access_token is not configured.")
        return
    try:
        info = threads_api_get("me", {"fields": "id,username,threads_profile_picture_url"}, token)
        print(f"✅ Meta Threads connected: @{info.get('username')} (ID: {info.get('id')})")
    except Exception as e:
        print(f"❌ Threads connection failed: {e}")


def test_twitter(config: Dict[str, Any]):
    ck = config.get("twitter_consumer_key", "")
    if not ck or "REPLACE" in ck:
        print("ℹ️ Twitter (X) API не настроен в config.json (требуются consumer key и access token).")
        return
    try:
        from twitter_client import verify_twitter
        info = verify_twitter(config)
        u_data = info.get("data", {})
        print(f"✅ Twitter (X) connected: @{u_data.get('username')} ({u_data.get('name')})")
    except Exception as e:
        print(f"❌ Twitter connection failed: {e}")


def main():
    parser = argparse.ArgumentParser(description="Telegram to Threads & Twitter Crossposter")
    parser.add_argument("--test-tg", action="store_true", help="Test Telegram Bot connectivity")
    parser.add_argument("--test-threads", action="store_true", help="Test Meta Threads API connectivity")
    parser.add_argument("--test-twitter", action="store_true", help="Test Twitter (X) API connectivity")
    parser.add_argument("--test-all", action="store_true", help="Test Telegram, Threads and Twitter")
    parser.add_argument("--dry-run", action="store_true", help="Run without posting")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in background")
    parser.add_argument("--once", action="store_true", help="Check for pending updates once and exit")
    args = parser.parse_args()

    config = load_config()

    if args.test_tg or args.test_all:
        test_telegram(config)
    if args.test_threads or args.test_all:
        test_threads(config)
    if args.test_twitter or args.test_all:
        test_twitter(config)

    if args.test_tg or args.test_threads or args.test_twitter or args.test_all:
        return

    run_poller(config, dry_run=args.dry_run, once=args.once)


if __name__ == "__main__":
    main()
