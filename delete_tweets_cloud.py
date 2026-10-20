#!/usr/bin/env python3
import asyncio
import os
import sys
import logging
from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("delete_tweets")

async def delete_all_tweets():
    auth_token = os.environ.get("TWITTER_AUTH_TOKEN")
    ct0 = os.environ.get("TWITTER_CT0")

    if not auth_token or not ct0:
        # Fallback to config.json if present
        try:
            import json
            with open("config.json") as f:
                cfg = json.load(f)
                cookies = cfg.get("twitter_cookies", {})
                auth_token = auth_token or cookies.get("auth_token")
                ct0 = ct0 or cookies.get("ct0")
        except Exception:
            pass

    if not auth_token or not ct0:
        logger.error("Missing TWITTER_AUTH_TOKEN or TWITTER_CT0")
        return

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-infobars"]
        )
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 800},
            locale="en-US"
        )
        for domain in [".x.com", ".twitter.com"]:
            await context.add_cookies([
                {"name": "auth_token", "value": auth_token, "domain": domain, "path": "/", "httpOnly": True, "secure": True},
                {"name": "ct0", "value": ct0, "domain": domain, "path": "/", "secure": True}
            ])
        page = await context.new_page()

        tweet_ids = [
            "2112651358918914425",
            "2112651705779094012",
            "2112662215769927970",
            "2112674958988218406",
            "2112664794289598923"
        ]

        for tid in tweet_ids:
            logger.info(f"Opening tweet {tid}...")
            try:
                await page.goto(f"https://x.com/m_dremin/status/{tid}", wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(3000)
                caret = await page.wait_for_selector('button[data-testid="caret"]', timeout=8000)
                if caret:
                    await caret.click()
                    await page.wait_for_timeout(1000)
                    del_btn = await page.wait_for_selector('//div[@role="menuitem"][contains(., "Delete") or contains(., "Удалить")]', timeout=5000)
                    if del_btn:
                        await del_btn.click()
                        await page.wait_for_timeout(1000)
                        confirm = await page.wait_for_selector('button[data-testid="confirmationSheetConfirm"]', timeout=5000)
                        if confirm:
                            await confirm.click()
                            logger.info(f"Deleted tweet {tid}")
                            await page.wait_for_timeout(2000)
            except Exception as e:
                logger.warning(f"Could not delete tweet {tid}: {e}")

        # Also clean up any extra tweets on profile
        logger.info("Checking profile https://x.com/m_dremin for any remaining recent tweets...")
        try:
            await page.goto("https://x.com/m_dremin", wait_until="domcontentloaded", timeout=25000)
            await page.wait_for_timeout(4000)
            for _ in range(6):
                caret = await page.query_selector('button[data-testid="caret"]')
                if not caret:
                    break
                await caret.click()
                await page.wait_for_timeout(1000)
                del_btn = await page.query_selector('//div[@role="menuitem"][contains(., "Delete") or contains(., "Удалить")]')
                if del_btn:
                    await del_btn.click()
                    await page.wait_for_timeout(1000)
                    confirm = await page.wait_for_selector('button[data-testid="confirmationSheetConfirm"]', timeout=5000)
                    if confirm:
                        await confirm.click()
                        logger.info("Deleted extra tweet from profile")
                        await page.wait_for_timeout(2000)
                else:
                    break
        except Exception as e:
            logger.warning(f"Profile cleanup exception: {e}")

        await browser.close()
        logger.info("Twitter cleanup finished.")

if __name__ == "__main__":
    asyncio.run(delete_all_tweets())
