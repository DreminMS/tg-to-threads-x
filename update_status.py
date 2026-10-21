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


def format_msk(dt_val: str) -> str:
    if not dt_val:
        return ""
    try:
        if "UTC+03:00" in dt_val or "MSK" in dt_val or "МСК" in dt_val:
            base = dt_val.split(" UTC")[0].split(" MSK")[0].split(" МСК")[0].strip()
            parts = base.split(":")
            if len(parts) == 3:
                base = f"{parts[0]}:{parts[1]}"
            return f"{base} МСК"
        clean = dt_val.replace("Z", "+00:00")
        if "T" not in clean and "+" not in clean and "-" in clean:
            dt = datetime.datetime.fromisoformat(clean).replace(tzinfo=datetime.timezone.utc)
        else:
            dt = datetime.datetime.fromisoformat(clean)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
        msk_tz = datetime.timezone(datetime.timedelta(hours=3))
        dt_msk = dt.astimezone(msk_tz)
        return dt_msk.strftime("%d.%m.%Y %H:%M МСК")
    except Exception:
        return str(dt_val)


def compute_schedule_slots(last_pub_raw: str, count: int = 5):
    msk_tz = datetime.timezone(datetime.timedelta(hours=3))
    now = datetime.datetime.now(msk_tz)

    min_time = now
    if last_pub_raw:
        try:
            clean = last_pub_raw.replace("Z", "+00:00")
            if "T" not in clean and "+" not in clean and "-" in clean:
                last_dt = datetime.datetime.fromisoformat(clean).replace(tzinfo=datetime.timezone.utc)
            else:
                last_dt = datetime.datetime.fromisoformat(clean)
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=datetime.timezone.utc)
            last_msk = last_dt.astimezone(msk_tz)
            earliest = last_msk + datetime.timedelta(minutes=50)
            if earliest > min_time:
                min_time = earliest
        except Exception:
            pass

    candidate = now.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)
    if candidate < min_time:
        candidate = min_time.replace(minute=0, second=0, microsecond=0) + datetime.timedelta(hours=1)

    slots = []
    curr = candidate
    while len(slots) < count:
        h = curr.hour
        # Schedule pause: 01:00 to 06:59 MSK
        if 1 <= h < 7:
            curr = curr.replace(hour=7, minute=0, second=0, microsecond=0)
            h = 7

        day_diff = (curr.date() - now.date()).days
        if day_diff == 0:
            prefix = "Сегодня"
        elif day_diff == 1:
            prefix = "Завтра"
        else:
            prefix = curr.strftime("%d.%m")

        time_str = curr.strftime("%H:%M")
        slots.append({
            "iso": curr.isoformat(),
            "time_msk": f"{time_str} МСК",
            "full_label": f"{prefix} в {time_str} МСК",
            "short_label": f"~{time_str} МСК"
        })
        curr = curr + datetime.timedelta(hours=1)

    return slots


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

    c.execute("SELECT max(published_at) FROM archive_posts WHERE status = 'published'")
    last_pub_row = c.fetchone()
    last_pub = last_pub_row[0] if last_pub_row else None

    # Fetch next upcoming posts (e.g. 5 upcoming posts for preview)
    c.execute("""
        SELECT post_id, post_date, photos_json, text, is_meme
        FROM archive_posts 
        WHERE status = 'pending' 
        ORDER BY post_id ASC 
        LIMIT 5
    """)
    upcoming_rows = c.fetchall()

    slots = compute_schedule_slots(last_pub, max(len(upcoming_rows), 1))

    upcoming_posts = []
    for idx, r in enumerate(upcoming_rows):
        photos = json.loads(r[2]) if r[2] else []
        slot = slots[idx] if idx < len(slots) else None
        upcoming_posts.append({
            "id": r[0],
            "date": r[1][:10] if r[1] else "",
            "date_msk": format_msk(r[1]),
            "photos_count": len(photos),
            "photos": photos[:4],  # first 4 photos for gallery preview
            "text": (r[3] or "").strip(),
            "is_meme": bool(r[4]),
            "tg_url": f"https://t.me/dremin_live/{r[0]}",
            "eta_msk": slot["time_msk"] if slot else "",
            "eta_label": slot["full_label"] if slot else "",
            "eta_iso": slot["iso"] if slot else ""
        })

    next_post = upcoming_posts[0] if upcoming_posts else None

    try:
        c.execute("ALTER TABLE archive_posts ADD COLUMN threads_url TEXT")
    except Exception:
        pass

    c.execute("""
        SELECT post_id, threads_id, twitter_id, published_at, photos_json, text, post_date, threads_url 
        FROM archive_posts 
        WHERE status = 'published' AND length(photos_json) > 2
        ORDER BY published_at DESC, post_id DESC 
        LIMIT 10
    """)
    recent_rows = c.fetchall()
    recent = []
    for r in recent_rows:
        photos = json.loads(r[4]) if r[4] else []
        th_url = r[7] if r[7] else (f"https://www.threads.net/@dremin" if r[1] else "")
        recent.append({
            "post_id": r[0],
            "threads_id": r[1],
            "twitter_id": r[2],
            "published_at": r[3],
            "published_at_msk": format_msk(r[3]),
            "date": r[6][:10] if r[6] else "",
            "date_msk": format_msk(r[6]),
            "photos_count": len(photos),
            "photos": photos[:4],
            "text": (r[5] or "").strip(),
            "threads_url": th_url,
            "twitter_url": f"https://x.com/m_dremin/status/{r[2]}" if r[2] else "",
            "tg_url": f"https://t.me/dremin_live/{r[0]}"
        })

    try:
        from analytics import get_analytics_summary
        analytics_data = get_analytics_summary()
    except Exception as e:
        analytics_data = {}

    msk_now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3)))
    status_data = {
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "updated_at_msk": msk_now.strftime("%H:%M МСК"),
        "timezone": "Europe/Moscow (МСК, UTC+3)",
        "next_publication": slots[0] if slots else None,
        "published": published,
        "pending": pending,
        "failed": failed,
        "skipped": skipped,
        "total": total,
        "percent": percent,
        "days_left": round(pending / 24, 1),
        "analytics": analytics_data,
        "next_post": next_post,
        "upcoming_posts": upcoming_posts,
        "recent_posts": recent,
        "workflows": [
            {
                "id": "archive_poster.yml",
                "name": "Hourly Archive Poster",
                "schedule": "Раз в час (07:00 - 00:00 МСК)",
                "description": f"Следующая публикация: {slots[0]['full_label']}" if slots else "Публикация архива с 2022 года",
                "next_run_msk": slots[0]["full_label"] if slots else ""
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
