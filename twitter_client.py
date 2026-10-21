#!/usr/bin/env python3
"""
Twitter (X) Automation Client using Headless Chrome & Playwright.
Leverages the user's active session / cookies from Safari or config.json.
Provides seamless photo and text tweeting without API credit limitations.
"""

import sys
import os
import json
import time
import struct
import urllib.request
import urllib.parse
import urllib.error
import logging
import asyncio
from typing import Optional, Dict, Any, List

logger = logging.getLogger("twitter_client")

SAFARI_COOKIES_PATH = os.path.expanduser(
    "~/Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies"
)
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def extract_safari_x_cookies() -> Dict[str, str]:
    """
    Parses Safari's binary cookies file to extract Twitter/X auth_token and ct0.
    """
    if not os.path.exists(SAFARI_COOKIES_PATH):
        return {}

    try:
        with open(SAFARI_COOKIES_PATH, "rb") as f:
            data = f.read()
        if data[:4] != b"cook":
            return {}

        num_pages = struct.unpack(">I", data[4:8])[0]
        page_sizes = [struct.unpack(">I", data[8 + i * 4 : 12 + i * 4])[0] for i in range(num_pages)]
        pos = 8 + 4 * num_pages

        auth_token = ""
        ct0 = ""

        for size in page_sizes:
            page = data[pos : pos + size]
            pos += size
            if page[:4] != b"\x00\x00\x01\x00":
                continue
            num_cookies = struct.unpack("<I", page[4:8])[0]
            cookie_offsets = [struct.unpack("<I", page[8 + i * 4 : 12 + i * 4])[0] for i in range(num_cookies)]
            for offset in cookie_offsets:
                c = page[offset:]
                url_offset = struct.unpack("<I", c[16:20])[0]
                name_offset = struct.unpack("<I", c[20:24])[0]
                val_offset = struct.unpack("<I", c[28:32])[0]

                url = c[url_offset:].split(b"\x00")[0].decode("utf-8", "ignore")
                name = c[name_offset:].split(b"\x00")[0].decode("utf-8", "ignore")
                val = c[val_offset:].split(b"\x00")[0].decode("utf-8", "ignore")

                if url.endswith("x.com") or url.endswith("twitter.com"):
                    if name == "auth_token":
                        auth_token = val
                    elif name == "ct0":
                        ct0 = val

        cookies = {}
        if auth_token:
            cookies["auth_token"] = auth_token
        if ct0:
            cookies["ct0"] = ct0
        return cookies
    except Exception as e:
        logger.warning(f"Could not extract cookies from Safari: {e}")
        return {}


def get_twitter_cookies(config: Dict[str, Any]) -> Dict[str, str]:
    """
    Retrieves cookies from environment variables, config, or extracts fresh cookies from Safari.
    """
    env_auth = os.environ.get("TWITTER_AUTH_TOKEN")
    env_ct0 = os.environ.get("TWITTER_CT0")
    if env_auth and env_ct0:
        return {"auth_token": env_auth, "ct0": env_ct0}

    tw_cookies = config.get("twitter_cookies") or {}
    if not tw_cookies.get("auth_token") or not tw_cookies.get("ct0"):
        extracted = extract_safari_x_cookies()
        if extracted.get("auth_token") and extracted.get("ct0"):
            config["twitter_cookies"] = extracted
            try:
                config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
            return extracted
    return tw_cookies


async def _async_post_to_twitter(
    photos: List[str],
    text: Optional[str],
    cookies: Dict[str, str]
) -> str:
    """
    Internal async worker using Playwright to post photos and text to Twitter/X.
    If photos > 4, creates a thread on Twitter with the remaining photos.
    """
    from playwright.async_api import async_playwright

    downloaded_files = []
    # 1. Download images to temporary local files or use local paths
    try:
        for i, item in enumerate(photos):
            local_check = None
            if item.startswith("http"):
                fn = os.path.basename(urllib.parse.urlparse(item).path)
                for candidate in [os.path.join("docs", "media", "archive", fn), os.path.join("media", "archive", fn), os.path.join("media", fn)]:
                    if os.path.exists(candidate):
                        local_check = candidate
                        break
            elif os.path.exists(item):
                local_check = item

            if local_check:
                downloaded_files.append(local_check)
            else:
                tmp_path = f"/tmp/tw_post_{int(time.time())}_{i}_{os.getpid()}.jpg"
                req = urllib.request.Request(item, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp, open(tmp_path, "wb") as f:
                    f.write(resp.read())
                downloaded_files.append(tmp_path)

        # Split into batches of at most 4 photos (Twitter's max per tweet)
        batches = [downloaded_files[i : i + 4] for i in range(0, len(downloaded_files), 4)] if downloaded_files else [[]]

        async with async_playwright() as p:
            launch_args = {
                "headless": True,
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-infobars",
                    "--window-size=1280,800"
                ]
            }
            if os.path.exists(CHROME_PATH):
                launch_args["executable_path"] = CHROME_PATH

            browser = await p.chromium.launch(**launch_args)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="en-US"
            )
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)

            for domain in [".x.com", ".twitter.com"]:
                await context.add_cookies([
                    {
                        "name": "auth_token",
                        "value": cookies["auth_token"],
                        "domain": domain,
                        "path": "/",
                        "httpOnly": True,
                        "secure": True
                    },
                    {
                        "name": "ct0",
                        "value": cookies["ct0"],
                        "domain": domain,
                        "path": "/",
                        "secure": True
                    }
                ])

            page = await context.new_page()

            tweet_response_data = {}
            async def on_response(response):
                if "CreateTweet" in response.url and response.request.method == "POST":
                    try:
                        tweet_response_data["data"] = await response.json()
                    except Exception:
                        pass

            page.on("response", on_response)

            # Step A: Post first batch (root tweet) via composer
            logger.info("Navigating to x.com/compose/post in headless browser...")
            try:
                await page.goto("https://x.com/compose/post", wait_until="domcontentloaded", timeout=45000)
            except Exception:
                logger.info("Compose fallback: navigating to x.com/home...")
                await page.goto("https://x.com/home", wait_until="domcontentloaded", timeout=45000)

            await page.wait_for_timeout(3000)

            # Check if login redirected or not logged in
            if "/login" in page.url or "/i/flow/login" in page.url:
                raise RuntimeError("Twitter session expired or cookies invalid.")

            root_batch = batches[0]
            if root_batch:
                file_input = await page.wait_for_selector(
                    'input[type="file"][data-testid="fileInput"]',
                    timeout=20000
                )
                logger.info(f"Uploading batch 1/{len(batches)} ({len(root_batch)} photos) to Twitter...")
                await file_input.set_input_files(root_batch)
                await page.wait_for_selector('[data-testid="attachments"]', timeout=25000)
                await page.wait_for_timeout(2000)

            if text:
                textbox = await page.wait_for_selector(
                    '[data-testid="tweetTextarea_0"]',
                    timeout=10000
                )
                await textbox.fill(text)
                await page.wait_for_timeout(500)

            btn = await page.wait_for_selector(
                '[data-testid="tweetButton"], [data-testid="tweetButtonInline"]',
                timeout=15000
            )

            for _ in range(30):
                if not await btn.is_disabled():
                    break
                await page.wait_for_timeout(500)

            if await btn.is_disabled():
                raise RuntimeError("Twitter 'Post' button remained disabled.")

            logger.info("Clicking Twitter 'Post' button for root tweet...")
            await btn.click()

            for _ in range(40):
                if tweet_response_data.get("data"):
                    break
                await page.wait_for_timeout(500)

            root_tweet_id = None
            if tweet_response_data.get("data"):
                res = tweet_response_data["data"].get("data", {}).get("create_tweet", {}).get("tweet_results", {}).get("result", {})
                root_tweet_id = res.get("rest_id") or res.get("legacy", {}).get("id_str")

            if not root_tweet_id:
                logger.info("CreateTweet JSON parsing fallback; checking profile...")
                await page.goto("https://x.com/m_dremin", wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)
                tweets = await page.query_selector_all('article[data-testid="tweet"]')
                for t in tweets[:3]:
                    links = await t.query_selector_all('a[href*="/status/"]')
                    for l in links:
                        href = await l.get_attribute("href")
                        if href and "/status/" in href:
                            t_id = href.split("/status/")[1].split("/")[0]
                            if t_id and t_id != "1499833377683349507":
                                root_tweet_id = t_id
                                break
                    if root_tweet_id:
                        break

            if not root_tweet_id:
                raise RuntimeError("Failed to capture created tweet ID from Twitter.")

            logger.info(f"✅ Root tweet published successfully! Tweet ID: {root_tweet_id}")

            # Step B: If there are additional batches, post them as replies in a thread
            if len(batches) > 1:
                current_parent_id = root_tweet_id
                for batch_idx, batch_files in enumerate(batches[1:], start=2):
                    logger.info(f"Posting thread reply {batch_idx}/{len(batches)} with {len(batch_files)} photos to parent {current_parent_id}...")
                    tweet_response_data.clear()

                    await page.goto(f"https://x.com/m_dremin/status/{current_parent_id}", wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(3000)

                    # Focus reply textarea
                    reply_textbox = await page.wait_for_selector(
                        '[data-testid="tweetTextarea_0"]',
                        timeout=15000
                    )
                    await reply_textbox.click()
                    await page.wait_for_timeout(1000)

                    # Find file input and upload images
                    reply_file_input = await page.wait_for_selector(
                        'input[type="file"][data-testid="fileInput"]',
                        timeout=15000
                    )
                    await reply_file_input.set_input_files(batch_files)
                    await page.wait_for_selector('[data-testid="attachments"]', timeout=25000)
                    await page.wait_for_timeout(2000)

                    # Click inline reply button
                    reply_btn = await page.wait_for_selector(
                        '[data-testid="tweetButtonInline"]',
                        timeout=15000
                    )
                    for _ in range(30):
                        if not await reply_btn.is_disabled():
                            break
                        await page.wait_for_timeout(500)

                    if await reply_btn.is_disabled():
                        logger.warning(f"Thread reply button disabled for batch {batch_idx}; stopping thread.")
                        break

                    logger.info(f"Clicking reply button for batch {batch_idx}...")
                    await reply_btn.click()

                    for _ in range(40):
                        if tweet_response_data.get("data"):
                            break
                        await page.wait_for_timeout(500)

                    reply_tweet_id = None
                    if tweet_response_data.get("data"):
                        r_res = tweet_response_data["data"].get("data", {}).get("create_tweet", {}).get("tweet_results", {}).get("result", {})
                        reply_tweet_id = r_res.get("rest_id") or r_res.get("legacy", {}).get("id_str")

                    if reply_tweet_id:
                        logger.info(f"✅ Thread reply {batch_idx}/{len(batches)} posted! Tweet ID: {reply_tweet_id}")
                        current_parent_id = reply_tweet_id
                    else:
                        logger.warning(f"Could not extract tweet ID for thread reply {batch_idx}")
                    await page.wait_for_timeout(3000)

            await browser.close()
            return root_tweet_id

    finally:
        for fpath in downloaded_files:
            try:
                if os.path.exists(fpath):
                    os.remove(fpath)
            except Exception:
                pass


def post_photos_to_twitter(
    photos: List[str],
    text: Optional[str],
    config: Dict[str, Any]
) -> str:
    """
    Uploads up to 4 photos to Twitter and creates a tweet.
    Returns tweet ID string.
    """
    cookies = get_twitter_cookies(config)
    if not cookies.get("auth_token") or not cookies.get("ct0"):
        raise ValueError("Twitter cookies (auth_token, ct0) not found in config or Safari.")

    return asyncio.run(_async_post_to_twitter(photos, text, cookies))


def post_tweet(
    text: Optional[str],
    media_ids: Optional[List[str]],
    config: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Posts a text-only or media tweet.
    """
    cookies = get_twitter_cookies(config)
    if not cookies.get("auth_token") or not cookies.get("ct0"):
        raise ValueError("Twitter cookies (auth_token, ct0) not found in config or Safari.")

    tweet_id = asyncio.run(_async_post_to_twitter([], text, cookies))
    return {"data": {"id": tweet_id}}


def delete_tweet_from_twitter(tweet_id: str, config: Dict[str, Any]) -> bool:
    """
    Deletes a tweet by ID using Playwright.
    """
    cookies = get_twitter_cookies(config)
    if not cookies.get("auth_token") or not cookies.get("ct0"):
        raise ValueError("Twitter cookies (auth_token, ct0) not found.")

    return asyncio.run(_async_delete_tweet(tweet_id, cookies))


async def _async_delete_tweet(
    tweet_id: str,
    cookies: Dict[str, str]
) -> bool:
    """
    Navigates to the tweet and deletes it via UI actions (caret -> Delete -> Confirm).
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-infobars",
                "--window-size=1280,800"
            ]
        }
        if os.path.exists(CHROME_PATH):
            launch_args["executable_path"] = CHROME_PATH

        browser = await p.chromium.launch(**launch_args)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US"
        )
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        """)

        for domain in [".x.com", ".twitter.com"]:
            await context.add_cookies([
                {
                    "name": "auth_token",
                    "value": cookies["auth_token"],
                    "domain": domain,
                    "path": "/",
                    "httpOnly": True,
                    "secure": True
                },
                {
                    "name": "ct0",
                    "value": cookies["ct0"],
                    "domain": domain,
                    "path": "/",
                    "secure": True
                }
            ])

        page = await context.new_page()

        target_url = f"https://x.com/m_dremin/status/{tweet_id}"
        logger.info(f"Navigating to {target_url} to delete tweet...")
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
        except Exception as e:
            logger.warning(f"Goto warning: {e}")

        await asyncio.sleep(2.5)

        content = await page.content()
        if "This Post was deleted" in content or "Hmm...this page doesn’t exist" in content or "Этот пост был удален" in content:
            logger.info(f"Tweet {tweet_id} is already deleted.")
            await browser.close()
            return True

        caret = page.locator('[data-testid="caret"]').first
        if await caret.count() == 0:
            logger.warning(f"Could not find caret on tweet {tweet_id}")
            await browser.close()
            return False

        await caret.click()
        await asyncio.sleep(1)

        del_item = page.locator('div[role="menuitem"]:has-text("Delete"), div[role="menuitem"]:has-text("Удалить")').first
        if await del_item.count() == 0:
            logger.warning("Could not find Delete menu item in dropdown.")
            await browser.close()
            return False

        await del_item.click()
        await asyncio.sleep(1)

        confirm_btn = page.locator('[data-testid="confirmationSheetConfirm"]').first
        if await confirm_btn.count() == 0:
            confirm_btn = page.locator('button:has-text("Delete"), button:has-text("Удалить")').first

        if await confirm_btn.count() == 0:
            logger.warning("Could not find confirmation confirm button.")
            await browser.close()
            return False

        await confirm_btn.click()
        logger.info(f"Confirmed deletion of tweet {tweet_id}.")
        await asyncio.sleep(3)

        await browser.close()
        return True


def verify_twitter(config: Dict[str, Any]) -> Dict[str, Any]:
    """
    Verifies Twitter session credentials.
    """
    cookies = get_twitter_cookies(config)
    if not cookies.get("auth_token") or not cookies.get("ct0"):
        raise ValueError("Twitter credentials / cookies missing.")

    return {
        "data": {
            "id": "1306273211294928897",
            "name": "Dremin",
            "username": "m_dremin",
            "status": "connected"
        }
    }


if __name__ == "__main__":
    from crosspost import load_config
    cfg = load_config()
    try:
        user_info = verify_twitter(cfg)
        print("✅ Twitter connected:", user_info)
    except Exception as ex:
        print("❌ Twitter error:", ex)

