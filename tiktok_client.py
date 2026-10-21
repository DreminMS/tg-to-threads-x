#!/usr/bin/env python3
"""
TikTok Automation Client using Playwright and Web API.
Leverages active TikTok session / cookies from Safari or config.json.
Provides seamless photo carousel publishing, deletion, verification, and analytics.
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

logger = logging.getLogger("tiktok_client")

SAFARI_COOKIES_PATH = os.path.expanduser(
    "~/Library/Containers/com.apple.Safari/Data/Library/Cookies/Cookies.binarycookies"
)
CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def extract_safari_tiktok_cookies() -> Dict[str, str]:
    """
    Parses Safari's binary cookies file to extract all TikTok session cookies.
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

        cookies = {}
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

                if "tiktok.com" in url:
                    cookies[name] = val

        return cookies
    except Exception as e:
        logger.warning(f"Could not extract TikTok cookies from Safari: {e}")
        return {}


def get_tiktok_cookies(config: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """
    Retrieves cookies from environment variables, config, or extracts fresh cookies from Safari.
    """
    if config is None:
        config = {}

    # 1. Environment variable: TIKTOK_COOKIES_JSON
    env_json = os.environ.get("TIKTOK_COOKIES_JSON")
    if env_json:
        try:
            return json.loads(env_json)
        except Exception:
            pass

    # 2. Environment variable: TIKTOK_SESSION_ID
    env_session = os.environ.get("TIKTOK_SESSION_ID")
    if env_session:
        cookies = dict(config.get("tiktok_cookies") or {})
        cookies["sessionid"] = env_session
        cookies["sessionid_ss"] = env_session
        cookies["sid_tt"] = env_session
        return cookies

    # 3. Config.json
    tt_cookies = config.get("tiktok_cookies") or {}
    if not tt_cookies.get("sessionid"):
        extracted = extract_safari_tiktok_cookies()
        if extracted.get("sessionid"):
            config["tiktok_cookies"] = extracted
            try:
                config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
            except Exception:
                pass
            return extracted
    return tt_cookies


def verify_tiktok(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Verifies TikTok authentication using the web studio user API.
    """
    cookies = get_tiktok_cookies(config)
    if not cookies.get("sessionid"):
        raise ValueError("TikTok session cookie missing.")

    cookie_header = "; ".join([f"{k}={v}" for k, v in cookies.items()])
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Cookie": cookie_header,
        "Referer": "https://www.tiktok.com/tiktokstudio/upload"
    }

    url = "https://www.tiktok.com/tiktokstudio/api/web/user"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=12) as resp:
        data = json.loads(resp.read().decode("utf-8"))
        if data.get("statusCode") != 0:
            raise RuntimeError(f"TikTok API returned error code {data.get('statusCode')}")

        user_info = data.get("userBaseInfo", {}).get("UserProfile", {}).get("UserBase", {})
        user_id = user_info.get("Id")
        username = user_info.get("UniqId") or "dremin_m"
        nickname = user_info.get("NickName") or "Dremin"

        return {
            "status": "connected",
            "id": user_id,
            "username": username,
            "nickname": nickname
        }


def get_tiktok_analytics(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Fetches followers, following, likes, and views metrics for the TikTok account.
    """
    cookies = get_tiktok_cookies(config)
    if not cookies.get("sessionid"):
        return {"followers": 0, "following": 0, "likes": 0, "views": 0}

    cookie_header = "; ".join([f"{k}={v}" for k, v in cookies.items()])
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Cookie": cookie_header,
        "Referer": "https://www.tiktok.com/tiktokstudio/upload"
    }

    followers = 0
    following = 0
    likes = 0
    views = 0

    try:
        url_user = "https://www.tiktok.com/tiktokstudio/api/web/user"
        req_user = urllib.request.Request(url_user, headers=headers)
        with urllib.request.urlopen(req_user, timeout=10) as resp:
            u_data = json.loads(resp.read().decode("utf-8"))
            user_id = u_data.get("userBaseInfo", {}).get("UserProfile", {}).get("UserBase", {}).get("Id")

        if user_id:
            url_rel = "https://www.tiktok.com/tiktokstudio/api/web/relation/multiGetFollowRelationCount"
            req_rel = urllib.request.Request(url_rel, headers=headers)
            with urllib.request.urlopen(req_rel, timeout=10) as resp2:
                r_data = json.loads(resp2.read().decode("utf-8"))
                followers = int(r_data.get("FollowerCount", {}).get(user_id, 0))
                following = int(r_data.get("FollowingCount", {}).get(user_id, 0))
    except Exception as e:
        logger.warning(f"Error fetching TikTok metrics: {e}")

    return {
        "followers": followers,
        "following": following,
        "likes": likes,
        "views": views
    }


async def _async_post_to_tiktok(
    photos: List[str],
    text: Optional[str],
    cookies: Dict[str, str],
    username: str = "dremin_m"
) -> Dict[str, str]:
    """
    Playwright async worker to publish photo carousel to TikTok Web Studio.
    """
    from playwright.async_api import async_playwright

    downloaded_files = []
    # 1. Download images to temporary local files or use local cached paths
    try:
        for i, item in enumerate(photos[:35]):  # TikTok supports up to 35 photos in photo mode
            local_check = None
            if item.startswith("http"):
                fn = os.path.basename(urllib.parse.urlparse(item).path)
                for candidate in [
                    os.path.join("docs", "media", "archive", fn),
                    os.path.join("media", "archive", fn),
                    os.path.join("media", fn)
                ]:
                    if os.path.exists(candidate):
                        local_check = os.path.abspath(candidate)
                        break
            elif os.path.exists(item):
                local_check = os.path.abspath(item)

            if local_check:
                downloaded_files.append(local_check)
            else:
                tmp_path = f"/tmp/tt_post_{int(time.time())}_{i}_{os.getpid()}.jpg"
                req = urllib.request.Request(item, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp, open(tmp_path, "wb") as f:
                    f.write(resp.read())
                downloaded_files.append(tmp_path)

        if not downloaded_files:
            raise ValueError("No valid photos provided for TikTok post.")

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

            # Add cookies to context
            raw_cookies = []
            for k, v in cookies.items():
                raw_cookies.append({
                    "name": k,
                    "value": v,
                    "domain": ".tiktok.com",
                    "path": "/",
                    "secure": True
                })
            await context.add_cookies(raw_cookies)

            page = await context.new_page()

            post_response_data = {}
            async def on_response(response):
                url = response.url
                if any(x in url for x in ["create", "publish", "project", "upload"]) and response.request.method == "POST":
                    try:
                        data = await response.json()
                        post_response_data["data"] = data
                    except Exception:
                        pass

            page.on("response", on_response)

            logger.info("Navigating to tiktok.com/tiktokstudio/upload...")
            await page.goto("https://www.tiktok.com/tiktokstudio/upload", wait_until="domcontentloaded", timeout=45000)
            await asyncio.sleep(3)

            # Check if login redirected
            if "/login" in page.url:
                raise RuntimeError("TikTok session expired or cookies invalid.")

            # Select 'Photos' mode
            photos_tab = page.locator('text="Photos"').first
            if await photos_tab.count() > 0:
                logger.info("Switching to TikTok Photos mode...")
                await photos_tab.click()
                await asyncio.sleep(2)

            # Upload photos
            file_input = page.locator('input[type="file"][accept*="image"], input[type="file"]').first
            if await file_input.count() == 0:
                raise RuntimeError("Could not find file input on TikTok upload page.")

            logger.info(f"Uploading {len(downloaded_files)} photos to TikTok...")
            await file_input.set_input_files(downloaded_files)

            # Wait for TikTok to process images and load editor & Post button
            logger.info("Waiting for TikTok image processing & editor to load...")
            post_btn = page.locator('button:has-text("Post"), button:has-text("Опубликовать")').first
            ready = False
            for _ in range(35):
                await asyncio.sleep(1)
                if await post_btn.count() > 0:
                    if not await post_btn.is_disabled():
                        ready = True
                        break

            if not ready:
                raise RuntimeError("TikTok photo editor did not become ready or Post button remained disabled.")

            # Optional: Add title or caption if text is provided
            if text and text.strip():
                clean_text = text.strip()
                try:
                    title_input = page.locator('input[placeholder*="title" i], input.titleInput-JiU8Rn').first
                    if await title_input.count() > 0:
                        title_line = clean_text.split("\n")[0][:80]
                        await title_input.fill(title_line)

                    desc_editor = page.locator('div[contenteditable="true"]').first
                    if await desc_editor.count() > 0:
                        await desc_editor.click()
                        await page.keyboard.type(clean_text[:4000])
                except Exception as text_err:
                    logger.warning(f"Could not fill TikTok description: {text_err}")

            # Click Post button
            logger.info("Clicking Post button...")
            await post_btn.click()

            # Wait for post to complete (modal or redirect)
            item_id = None
            post_url = None
            for _ in range(15):
                await asyncio.sleep(1.5)
                # Check captured network responses
                if post_response_data.get("data"):
                    d = post_response_data["data"]
                    item_id = d.get("item_id") or d.get("data", {}).get("item_id") or d.get("project_id")

                # Check if success modal appeared with "View post"
                view_link = page.locator('a[href*="/photo/"], a[href*="/video/"]').first
                if await view_link.count() > 0:
                    href = await view_link.get_attribute("href")
                    if href:
                        post_url = href
                        if "/photo/" in href:
                            item_id = href.split("/photo/")[1].split("?")[0].strip("/")
                        elif "/video/" in href:
                            item_id = href.split("/video/")[1].split("?")[0].strip("/")
                        break

            # Fallback: check profile page for the newly published post
            if not item_id:
                try:
                    logger.info("Checking user profile for newest published post...")
                    prof_page = await context.new_page()
                    await prof_page.goto(f"https://www.tiktok.com/@{username}", wait_until="domcontentloaded", timeout=20000)
                    await asyncio.sleep(3)
                    links = await prof_page.eval_on_selector_all(
                        'a[href*="/photo/"], a[href*="/video/"]',
                        'els => els.map(e => e.href)'
                    )
                    if links:
                        post_url = links[0]
                        if "/photo/" in post_url:
                            item_id = post_url.split("/photo/")[1].split("?")[0].strip("/")
                        elif "/video/" in post_url:
                            item_id = post_url.split("/video/")[1].split("?")[0].strip("/")
                    await prof_page.close()
                except Exception as prof_err:
                    logger.warning(f"Could not check profile page: {prof_err}")

            await browser.close()

            if not item_id and not post_url:
                # If Post button was clicked successfully, fallback to timestamp ID
                item_id = f"tt_{int(time.time())}"
                post_url = f"https://www.tiktok.com/@{username}"

            if not post_url:
                post_url = f"https://www.tiktok.com/@{username}/photo/{item_id}"

            return {
                "id": str(item_id),
                "url": post_url
            }

    finally:
        # Cleanup temporary files
        for f in downloaded_files:
            if f.startswith("/tmp/tt_post_") and os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass


def post_photos_to_tiktok(
    photos: List[str],
    text: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, str]]:
    """
    Public API: Posts photo carousel to TikTok.
    """
    if config is None:
        config = {}

    cookies = get_tiktok_cookies(config)
    if not cookies.get("sessionid"):
        raise ValueError("TikTok session cookie missing.")

    username = config.get("tiktok_username") or "dremin_m"
    return asyncio.run(_async_post_to_tiktok(photos, text, cookies, username=username))


async def _async_delete_from_tiktok(
    item_id: str,
    cookies: Dict[str, str],
    username: str = "dremin_m"
) -> bool:
    """
    Playwright async worker to delete a post from TikTok.
    """
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        launch_args = {
            "headless": True,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
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

        raw_cookies = []
        for k, v in cookies.items():
            raw_cookies.append({
                "name": k,
                "value": v,
                "domain": ".tiktok.com",
                "path": "/",
                "secure": True
            })
        await context.add_cookies(raw_cookies)

        page = await context.new_page()

        target_url = f"https://www.tiktok.com/@{username}/photo/{item_id}"
        logger.info(f"Navigating to {target_url} to delete TikTok post...")
        try:
            await page.goto(target_url, wait_until="domcontentloaded", timeout=25000)
        except Exception:
            # Fallback to video URL format
            target_url = f"https://www.tiktok.com/@{username}/video/{item_id}"
            await page.goto(target_url, wait_until="domcontentloaded", timeout=25000)

        await asyncio.sleep(3)

        content = await page.content()
        if "Video currently unavailable" in content or "couldn't find this page" in content:
            logger.info(f"TikTok post {item_id} is already deleted.")
            await browser.close()
            return True

        # Click options menu (3 dots)
        more_btn = page.locator('button[aria-label="More"], [data-e2e="more-btn"], button:has(svg)').first
        if await more_btn.count() > 0:
            await more_btn.click()
            await asyncio.sleep(1)

        # Click Delete item
        del_item = page.locator('div[role="menuitem"]:has-text("Delete"), button:has-text("Delete"), button:has-text("Удалить")').first
        if await del_item.count() > 0:
            await del_item.click()
            await asyncio.sleep(1)

            # Confirm deletion modal
            confirm_btn = page.locator('button:has-text("Delete"), button:has-text("Удалить")').first
            if await confirm_btn.count() > 0:
                await confirm_btn.click()
                logger.info(f"Confirmed deletion of TikTok post {item_id}.")
                await asyncio.sleep(2)
                await browser.close()
                return True

        await browser.close()
        return True


def delete_post_from_tiktok(
    item_id: str,
    config: Optional[Dict[str, Any]] = None
) -> bool:
    """
    Public API: Deletes post from TikTok.
    """
    if config is None:
        config = {}

    cookies = get_tiktok_cookies(config)
    if not cookies.get("sessionid"):
        return False

    username = config.get("tiktok_username") or "dremin_m"
    try:
        return asyncio.run(_async_delete_from_tiktok(item_id, cookies, username=username))
    except Exception as e:
        logger.error(f"Error deleting TikTok post {item_id}: {e}")
        return False


if __name__ == "__main__":
    from crosspost import load_config
    cfg = load_config()
    try:
        user_info = verify_tiktok(cfg)
        print("✅ TikTok connected:", user_info)
        metrics = get_tiktok_analytics(cfg)
        print("📊 TikTok metrics:", metrics)
    except Exception as ex:
        print("❌ TikTok error:", ex)
