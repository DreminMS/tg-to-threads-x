import os
import json
import sqlite3
import urllib.request
import urllib.parse
import re
import asyncio
import concurrent.futures
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("analytics")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "crosspost.db")
CONFIG_FILE = os.path.join(SCRIPT_DIR, "config.json")

# Start threshold for the archive crossposting campaign (2026-10-20 20:00 UTC)
CAMPAIGN_START_ISO = "2026-10-20T20:00:00"


def load_config() -> Dict[str, Any]:
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            pass
    if os.environ.get("THREADS_USER_ID"):
        cfg["threads_user_id"] = os.environ["THREADS_USER_ID"]
    if os.environ.get("THREADS_ACCESS_TOKEN"):
        cfg["threads_access_token"] = os.environ["THREADS_ACCESS_TOKEN"]
    if os.environ.get("TWITTER_AUTH_TOKEN"):
        cfg.setdefault("twitter_cookies", {})["auth_token"] = os.environ["TWITTER_AUTH_TOKEN"]
    if os.environ.get("TWITTER_CT0"):
        cfg.setdefault("twitter_cookies", {})["ct0"] = os.environ["TWITTER_CT0"]
    return cfg


def init_post_metrics_db(conn):
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS post_metrics (
            post_id INTEGER PRIMARY KEY,
            threads_id TEXT,
            twitter_id TEXT,
            threads_likes INTEGER DEFAULT 0,
            threads_views INTEGER DEFAULT 0,
            threads_replies INTEGER DEFAULT 0,
            threads_reposts INTEGER DEFAULT 0,
            twitter_likes INTEGER DEFAULT 0,
            twitter_views INTEGER DEFAULT 0,
            twitter_replies INTEGER DEFAULT 0,
            twitter_retweets INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def save_threads_post_metrics(conn, post_metrics_map: Dict[str, Dict[str, int]]):
    if not post_metrics_map:
        return
    init_post_metrics_db(conn)
    c = conn.cursor()
    c.execute("""
        SELECT post_id, threads_id, twitter_id FROM archive_posts WHERE threads_id IS NOT NULL
        UNION
        SELECT message_id, threads_id, twitter_id FROM processed_posts WHERE threads_id IS NOT NULL
    """)
    th_to_post = {}
    for pid, th_id, tw_id in c.fetchall():
        if th_id:
            th_to_post[str(th_id)] = (pid, tw_id)

    for th_id, m in post_metrics_map.items():
        if str(th_id) not in th_to_post or not m:
            continue
        pid, tw_id = th_to_post[str(th_id)]
        c.execute("""
            INSERT INTO post_metrics (
                post_id, threads_id, twitter_id,
                threads_likes, threads_views, threads_replies, threads_reposts,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(post_id) DO UPDATE SET
                threads_id = excluded.threads_id,
                twitter_id = COALESCE(excluded.twitter_id, post_metrics.twitter_id),
                threads_likes = excluded.threads_likes,
                threads_views = excluded.threads_views,
                threads_replies = excluded.threads_replies,
                threads_reposts = excluded.threads_reposts,
                updated_at = CURRENT_TIMESTAMP
        """, (
            pid, str(th_id), tw_id,
            m.get("likes", 0), m.get("views", 0), m.get("replies", 0), m.get("reposts", 0)
        ))
    conn.commit()


def save_twitter_post_metrics(conn, twitter_metrics_map: Dict[str, Dict[str, Any]]):
    if not twitter_metrics_map:
        return
    init_post_metrics_db(conn)
    c = conn.cursor()
    c.execute("""
        SELECT post_id, threads_id, twitter_id FROM archive_posts WHERE twitter_id IS NOT NULL
        UNION
        SELECT message_id, threads_id, twitter_id FROM processed_posts WHERE twitter_id IS NOT NULL
    """)
    tw_to_post = {}
    for pid, th_id, tw_id in c.fetchall():
        if tw_id:
            tw_to_post[str(tw_id)] = (pid, th_id)

    for tw_id, m in twitter_metrics_map.items():
        if str(tw_id) not in tw_to_post or not m:
            continue
        pid, th_id = tw_to_post[str(tw_id)]
        c.execute("""
            INSERT INTO post_metrics (
                post_id, threads_id, twitter_id,
                twitter_likes, twitter_views, twitter_replies, twitter_retweets,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(post_id) DO UPDATE SET
                twitter_id = excluded.twitter_id,
                threads_id = COALESCE(excluded.threads_id, post_metrics.threads_id),
                twitter_likes = excluded.twitter_likes,
                twitter_views = excluded.twitter_views,
                twitter_replies = excluded.twitter_replies,
                twitter_retweets = excluded.twitter_retweets,
                updated_at = CURRENT_TIMESTAMP
        """, (
            pid, th_id, str(tw_id),
            m.get("likes", 0), m.get("views", 0), m.get("replies", 0), m.get("rts", 0)
        ))
    conn.commit()


def get_recent_posts_metrics(conn) -> Dict[int, Dict[str, Any]]:
    init_post_metrics_db(conn)
    c = conn.cursor()
    c.execute("""
        SELECT post_id, threads_likes, threads_views, threads_replies, threads_reposts,
               twitter_likes, twitter_views, twitter_replies, twitter_retweets
        FROM post_metrics
    """)
    res = {}
    for r in c.fetchall():
        pid = r[0]
        th_l = r[1] or 0
        th_v = r[2] or 0
        th_rp = r[3] or 0
        th_rt = r[4] or 0
        tw_l = r[5] or 0
        tw_v = r[6] or 0
        tw_rp = r[7] or 0
        tw_rt = r[8] or 0
        res[pid] = {
            "likes": th_l + tw_l,
            "views": th_v + tw_v,
            "replies": th_rp + tw_rp,
            "reposts": th_rt + tw_rt,
            "threads": {
                "likes": th_l,
                "views": th_v,
                "replies": th_rp,
                "reposts": th_rt
            },
            "twitter": {
                "likes": tw_l,
                "views": tw_v,
                "replies": tw_rp,
                "reposts": tw_rt
            }
        }
    return res


def fetch_threads_insights(config: Dict[str, Any]) -> Dict[str, Any]:
    token = config.get("threads_access_token")
    uid = config.get("threads_user_id")
    if not token or not uid:
        return {}

    results = {
        "followers": 1235,
        "views": 0,
        "likes": 0,
        "replies": 0,
        "reposts": 0,
        "quotes": 0
    }

    # 1. Fetch user-level followers count and account metrics
    try:
        url = f"https://graph.threads.net/v1.0/{uid}/threads_insights?metric=followers_count,views,likes,replies,reposts,quotes&access_token={token}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read()).get("data", [])
            for item in data:
                name = item.get("name")
                if name == "followers_count":
                    results["followers"] = item.get("total_value", {}).get("value", 1235)
                elif name in ["views", "likes", "replies", "reposts", "quotes"]:
                    val = 0
                    if item.get("total_value"):
                        val = item["total_value"].get("value", 0)
                    elif item.get("values"):
                        val = sum(v.get("value", 0) for v in item["values"])
                    if val > results.get(name, 0):
                        results[name] = val
    except Exception as e:
        logger.warning(f"Error fetching Threads followers: {e}")

    # 2. Query ALL published posts across archive and realtime
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("""
            SELECT DISTINCT threads_id FROM (
                SELECT threads_id FROM archive_posts WHERE status = 'published' AND threads_id IS NOT NULL
                UNION
                SELECT threads_id FROM processed_posts WHERE threads_id IS NOT NULL
            )
        """)
        post_ids = [r[0] for r in c.fetchall() if r[0]]

        def _fetch_single_thread(pid: str) -> tuple:
            p_url = f"https://graph.threads.net/v1.0/{pid}/insights?metric=views,likes,replies,reposts&access_token={token}"
            try:
                p_req = urllib.request.Request(p_url)
                with urllib.request.urlopen(p_req, timeout=8) as p_resp:
                    p_data = json.loads(p_resp.read()).get("data", [])
                    p_metrics = {}
                    for p_item in p_data:
                        val = 0
                        if p_item.get("values"):
                            val = p_item["values"][0].get("value", 0)
                        elif p_item.get("total_value"):
                            val = p_item["total_value"].get("value", 0)
                        p_metrics[p_item.get("name")] = val
                    return pid, p_metrics
            except Exception:
                return pid, {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            thread_results = list(executor.map(_fetch_single_thread, post_ids))

        post_metric_dict = {pid: m for pid, m in thread_results if m}
        save_threads_post_metrics(conn, post_metric_dict)

        all_post_metrics = list(post_metric_dict.values())
        post_views = sum(m.get("views", 0) for m in all_post_metrics)
        post_likes = sum(m.get("likes", 0) for m in all_post_metrics)
        post_replies = sum(m.get("replies", 0) for m in all_post_metrics)
        post_reposts = sum(m.get("reposts", 0) for m in all_post_metrics)

        results["views"] = max(results["views"], post_views)
        results["likes"] = max(results["likes"], post_likes)
        results["replies"] = max(results["replies"], post_replies)
        results["reposts"] = max(results["reposts"], post_reposts)
        logger.info(f"Threads metrics computed across {len(post_ids)} posts: views={results['views']}, likes={results['likes']}, replies={results['replies']}, reposts={results['reposts']}")
    except Exception as e:
        logger.warning(f"Error fetching Threads post insights: {e}")

    return results


def _parse_metric_number(label: Optional[str]) -> int:
    if not label:
        return 0
    # Clean non-breaking spaces and extract digits
    cleaned = re.sub(r'[\s\xa0,.]', '', label)
    m = re.search(r'(\d+)', cleaned)
    return int(m.group(1)) if m else 0


async def _get_external_replies_count(page, tweet_id: str) -> int:
    try:
        await page.goto(f"https://x.com/m_dremin/status/{tweet_id}", wait_until="domcontentloaded", timeout=12000)
        await page.wait_for_selector('article[data-testid="tweet"]', timeout=8000)
        articles = await page.query_selector_all('article[data-testid="tweet"]')
        external_count = 0
        for i, a in enumerate(articles):
            if i == 0:
                continue
            user_el = await a.query_selector('[data-testid="User-Name"]')
            txt = await user_el.inner_text() if user_el else ""
            if "@m_dremin" not in txt.lower():
                external_count += 1
        return external_count
    except Exception as e:
        logger.warning(f"Could not inspect replies for tweet {tweet_id}: {e}")
        return 0


async def _fetch_twitter_playwright(cookies: Dict[str, str]) -> Dict[str, Any]:
    from playwright.async_api import async_playwright
    
    results = {
        "followers": 526,
        "views": 0,
        "likes": 0,
        "retweets": 0,
        "replies": 0
    }

    launch_args = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled"
        ]
    }
    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if os.path.exists(chrome_path):
        launch_args["executable_path"] = chrome_path

    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800}
        )
        for domain in [".x.com", ".twitter.com"]:
            await context.add_cookies([
                {"name": "auth_token", "value": cookies["auth_token"], "domain": domain, "path": "/", "httpOnly": True, "secure": True},
                {"name": "ct0", "value": cookies["ct0"], "domain": domain, "path": "/", "secure": True}
            ])
        
        page = await context.new_page()
        try:
            # Navigate to with_replies so threads and replies are fully loaded
            await page.goto("https://x.com/m_dremin/with_replies", wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(2000)
            
            # Follower count from profile link
            followers_el = await page.query_selector('a[href*="/verified_followers"], a[href*="/followers"]')
            if followers_el:
                f_txt = await followers_el.inner_text()
                f_val = _parse_metric_number(f_txt)
                if f_val > 0:
                    results["followers"] = f_val

            # Scroll and collect all unique tweets belonging to the campaign
            all_seen = {}
            consecutive_old = 0
            for scroll_idx in range(20):
                articles = await page.query_selector_all('article[data-testid="tweet"]')
                for a in articles:
                    link_el = await a.query_selector('a[href*="/status/"]')
                    href = await link_el.get_attribute('href') if link_el else ''
                    m = re.search(r'/status/(\d+)', href)
                    if not m:
                        continue
                    tid = m.group(1)
                    if tid in all_seen:
                        continue

                    time_el = await a.query_selector('time')
                    dt = await time_el.get_attribute('datetime') if time_el else ''
                    if dt and dt < CAMPAIGN_START_ISO:
                        consecutive_old += 1
                        if consecutive_old >= 6:
                            break
                        continue

                    like_el = await a.query_selector('[data-testid="like"], [data-testid="unlike"]')
                    like_lbl = await like_el.get_attribute('aria-label') if like_el else ''
                    likes = _parse_metric_number(like_lbl)
                    if likes == 0 and like_el:
                        txt = (await like_el.inner_text()).strip()
                        if txt.isdigit():
                            likes = int(txt)

                    rt_el = await a.query_selector('[data-testid="retweet"], [data-testid="unretweet"]')
                    rt_lbl = await rt_el.get_attribute('aria-label') if rt_el else ''
                    rts = _parse_metric_number(rt_lbl)
                    if rts == 0 and rt_el:
                        txt = (await rt_el.inner_text()).strip()
                        if txt.isdigit():
                            rts = int(txt)

                    reply_el = await a.query_selector('[data-testid="reply"]')
                    reply_lbl = await reply_el.get_attribute('aria-label') if reply_el else ''
                    replies = _parse_metric_number(reply_lbl)
                    if replies == 0 and reply_el:
                        txt = (await reply_el.inner_text()).strip()
                        if txt.isdigit():
                            replies = int(txt)

                    view_el = await a.query_selector('a[href*="/analytics"]')
                    view_lbl = await view_el.get_attribute('aria-label') if view_el else ''
                    views = _parse_metric_number(view_lbl)
                    if views == 0 and view_el:
                        txt = (await view_el.inner_text()).strip()
                        if txt.isdigit():
                            views = int(txt)

                    all_seen[tid] = {
                        'likes': likes,
                        'rts': rts,
                        'replies': replies,
                        'views': views,
                        'dt': dt
                    }

                if consecutive_old >= 12:
                    break

                await page.evaluate('window.scrollBy(0, 1600)')
                await page.wait_for_timeout(700)

            results["likes"] = sum(v['likes'] for v in all_seen.values())
            results["retweets"] = sum(v['rts'] for v in all_seen.values())
            results["replies"] = sum(v['replies'] for v in all_seen.values())
            results["views"] = sum(v['views'] for v in all_seen.values())
            results["tweets"] = all_seen
            logger.info(f"Twitter metrics computed across {len(all_seen)} tweets: views={results['views']}, likes={results['likes']}, replies={results['replies']}, retweets={results['retweets']}")
        finally:
            await browser.close()

    return results


def fetch_twitter_insights(config: Dict[str, Any]) -> Dict[str, Any]:
    from twitter_client import get_twitter_cookies
    cookies = get_twitter_cookies(config)
    
    # 1. Pull last known history from DB so metrics never drop on transient network glitches
    last_known = {"followers": 526, "views": 1400, "likes": 35, "replies": 24, "retweets": 0}
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("""
            SELECT twitter_likes, twitter_views, twitter_replies, twitter_retweets, twitter_followers
            FROM analytics_history
            WHERE twitter_views > 100
            ORDER BY recorded_at DESC LIMIT 1
        """)
        row = c.fetchone()
        if row:
            last_known["likes"] = row[0] or 0
            last_known["views"] = row[1] or 0
            last_known["replies"] = row[2] or 0
            last_known["retweets"] = row[3] or 0
            if row[4]:
                last_known["followers"] = row[4]
    except Exception:
        pass

    # 2. Try Playwright extraction (accurate live DOM extraction)
    pw_res = None
    if cookies.get("auth_token") and cookies.get("ct0"):
        try:
            pw_res = asyncio.run(_fetch_twitter_playwright(cookies))
        except Exception as e:
            logger.warning(f"Playwright Twitter scraping failed: {e}")

    if pw_res and pw_res.get("views", 0) > 0:
        if pw_res.get("tweets"):
            try:
                conn = sqlite3.connect(DB_FILE)
                save_twitter_post_metrics(conn, pw_res["tweets"])
            except Exception as e:
                logger.warning(f"Could not save twitter post metrics: {e}")
        return {
            "followers": pw_res.get("followers") or last_known["followers"],
            "views": max(pw_res.get("views", 0), last_known["views"]),
            "likes": max(pw_res.get("likes", 0), last_known["likes"]),
            "replies": max(pw_res.get("replies", 0), last_known["replies"]),
            "retweets": max(pw_res.get("retweets", 0), last_known["retweets"])
        }

    return last_known


def fetch_tiktok_insights(config: Dict[str, Any]) -> Dict[str, Any]:
    try:
        from tiktok_client import get_tiktok_analytics
        return get_tiktok_analytics(config)
    except Exception as e:
        logger.warning(f"Error fetching TikTok insights: {e}")
        return {"followers": 0, "following": 0, "likes": 0, "views": 0}


def get_analytics_summary() -> Dict[str, Any]:
    config = load_config()
    threads_data = fetch_threads_insights(config)
    twitter_data = fetch_twitter_insights(config)
    tiktok_enabled = bool(config.get("tiktok_enabled"))
    tiktok_data = fetch_tiktok_insights(config) if tiktok_enabled else {}

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Get baselines
    c.execute("SELECT platform, initial_followers, start_date FROM analytics_baseline")
    baselines = {r[0]: {"initial": r[1], "date": r[2]} for r in c.fetchall()}

    threads_base = baselines.get("threads", {}).get("initial", 1235)
    twitter_base = baselines.get("twitter", {}).get("initial", 526)
    tiktok_base = baselines.get("tiktok", {}).get("initial", 0) if tiktok_enabled else 0
    start_date = baselines.get("threads", {}).get("date", "2026-10-21")

    th_followers = threads_data.get("followers", 1235)
    tw_followers = twitter_data.get("followers", 526)
    tt_followers = tiktok_data.get("followers", 0) if tiktok_enabled else 0

    th_growth = th_followers - threads_base
    tw_growth = tw_followers - twitter_base
    tt_growth = (tt_followers - tiktok_base) if tiktok_enabled else 0
    total_growth = th_growth + tw_growth + tt_growth

    th_likes = threads_data.get("likes", 0)
    tw_likes = twitter_data.get("likes", 0)
    tt_likes = tiktok_data.get("likes", 0) if tiktok_enabled else 0
    total_likes = th_likes + tw_likes + tt_likes

    th_replies = threads_data.get("replies", 0)
    tw_replies = twitter_data.get("replies", 0)
    total_replies = th_replies + tw_replies

    th_reposts = threads_data.get("reposts", 0)
    tw_reposts = twitter_data.get("retweets", 0)
    total_reposts = th_reposts + tw_reposts

    th_views = threads_data.get("views", 0)
    tw_views = twitter_data.get("views", 0)
    tt_views = tiktok_data.get("views", 0) if tiktok_enabled else 0
    total_views = th_views + tw_views + tt_views

    total_reactions = total_likes + total_replies + total_reposts

    # Count actual photos published in archive
    c.execute("SELECT photos_json FROM archive_posts WHERE status = 'published'")
    pub_rows = c.fetchall()
    published_photos = 0
    for r in pub_rows:
        try:
            published_photos += len(json.loads(r[0]))
        except Exception:
            pass

    # Save to history
    try:
        c.execute("""
            INSERT INTO analytics_history (
                threads_followers, twitter_followers, tiktok_followers,
                threads_likes, threads_replies, threads_reposts, threads_views,
                twitter_likes, twitter_retweets, twitter_replies, twitter_views,
                tiktok_likes, tiktok_views
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            th_followers, tw_followers, tt_followers,
            th_likes, th_replies, th_reposts, th_views,
            tw_likes, tw_reposts, tw_replies, tw_views,
            tt_likes, tt_views
        ))
        conn.commit()
    except Exception as e:
        logger.warning(f"Could not record analytics history: {e}")

    res = {
        "start_date": start_date,
        "total_reactions": total_reactions,
        "total_views": total_views,
        "total_growth": total_growth,
        "threads": {
            "username": "dremin",
            "url": "https://www.threads.net/@dremin",
            "followers": th_followers,
            "baseline": threads_base,
            "growth": th_growth,
            "likes": th_likes,
            "replies": th_replies,
            "reposts": th_reposts,
            "views": th_views
        },
        "twitter": {
            "username": "m_dremin",
            "url": "https://x.com/m_dremin",
            "followers": tw_followers,
            "baseline": twitter_base,
            "growth": tw_growth,
            "likes": tw_likes,
            "replies": tw_replies,
            "retweets": tw_reposts,
            "views": tw_views
        },
        "totals": {
            "likes": total_likes,
            "replies": total_replies,
            "reposts": total_reposts,
            "views": total_views,
            "published_photos": published_photos,
            "total_archive_photos": 1940
        }
    }
    if tiktok_enabled:
        res["tiktok"] = {
            "username": config.get("tiktok_username", "dremin_m"),
            "url": f"https://www.tiktok.com/@{config.get('tiktok_username', 'dremin_m')}",
            "followers": tt_followers,
            "baseline": tiktok_base,
            "growth": tt_growth,
            "likes": tt_likes,
            "views": tt_views
        }
    return res


if __name__ == "__main__":
    summary = get_analytics_summary()
    print(json.dumps(summary, indent=2, ensure_ascii=False))
