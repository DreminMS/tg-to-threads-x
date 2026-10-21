#!/usr/bin/env python3
"""
Exports database queue status to docs/status.json for the mobile PWA.
"""

import sqlite3
import json
import os
import datetime

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "crosspost.db")
DOCS_DIR = os.path.join(SCRIPT_DIR, "docs")
STATUS_FILE = os.path.join(DOCS_DIR, "status.json")


def update_status_json():
    if not os.path.exists(DB_FILE):
        return

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT count(*) FROM archive_posts WHERE status = 'published'")
    published = c.fetchone()[0]

    c.execute("SELECT count(*) FROM archive_posts WHERE status = 'pending'")
    pending = c.fetchone()[0]

    c.execute("SELECT count(*) FROM archive_posts WHERE status = 'failed'")
    failed = c.fetchone()[0]

    c.execute("SELECT count(*) FROM archive_posts WHERE status = 'skipped'")
    skipped = c.fetchone()[0]

    total = published + pending + failed + skipped
    percent = round((published / total * 100), 1) if total > 0 else 0.0

    # Fetch next upcoming posts (e.g. 5 upcoming posts for preview)
    c.execute("""
        SELECT post_id, post_date, photos_json, text, is_meme
        FROM archive_posts 
        WHERE status = 'pending' 
        ORDER BY post_id ASC 
        LIMIT 5
    """)
    upcoming_rows = c.fetchall()
    upcoming_posts = []
    for r in upcoming_rows:
        photos = json.loads(r[2]) if r[2] else []
        upcoming_posts.append({
            "id": r[0],
            "date": r[1][:10] if r[1] else "",
            "photos_count": len(photos),
            "photos": photos[:4],  # first 4 photos for gallery preview
            "text": (r[3] or "").strip(),
            "is_meme": bool(r[4]),
            "tg_url": f"https://t.me/dremin_live/{r[0]}"
        })

    next_post = upcoming_posts[0] if upcoming_posts else None

    c.execute("""
        SELECT post_id, threads_id, twitter_id, published_at 
        FROM archive_posts 
        WHERE status = 'published' 
        ORDER BY published_at DESC 
        LIMIT 6
    """)
    recent_rows = c.fetchall()
    recent = []
    for r in recent_rows:
        recent.append({
            "post_id": r[0],
            "threads_id": r[1],
            "twitter_id": r[2],
            "published_at": r[3]
        })

    status_data = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "published": published,
        "pending": pending,
        "failed": failed,
        "skipped": skipped,
        "total": total,
        "percent": percent,
        "days_left": round(pending / 24, 1),
        "next_post": next_post,
        "upcoming_posts": upcoming_posts,
        "recent_posts": recent,
        "workflows": [
            {
                "id": "archive_poster.yml",
                "name": "Hourly Archive Poster",
                "schedule": "Раз в час (XX:00)",
                "description": "Публикация архива с 2022 года"
            },
            {
                "id": "crosspost.yml",
                "name": "Realtime Telegram Crossposter",
                "schedule": "Каждые 10 минут",
                "description": "Проверка новых постов в @dremin_live"
            }
        ]
    }

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False)

    root_status = os.path.join(SCRIPT_DIR, "status.json")
    with open(root_status, "w", encoding="utf-8") as f:
        json.dump(status_data, f, indent=2, ensure_ascii=False)

    print(f"Status exported to {STATUS_FILE} and {root_status}")


if __name__ == "__main__":
    update_status_json()
