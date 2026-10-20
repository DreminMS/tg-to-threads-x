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

    total = published + pending + failed
    percent = round((published / total * 100), 1) if total > 0 else 0.0

    c.execute("""
        SELECT post_id, post_date, photos_json 
        FROM archive_posts 
        WHERE status = 'pending' 
        ORDER BY post_id ASC 
        LIMIT 1
    """)
    next_row = c.fetchone()
    next_post = None
    if next_row:
        photos = json.loads(next_row[2]) if next_row[2] else []
        next_post = {
            "id": next_row[0],
            "date": next_row[1][:10] if next_row[1] else "",
            "photos_count": len(photos)
        }

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
        "total": total,
        "percent": percent,
        "days_left": round(pending / 24, 1),
        "next_post": next_post,
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

    print(f"Status exported to {STATUS_FILE}")


if __name__ == "__main__":
    update_status_json()
