#!/usr/bin/env python3
"""
Analyzes photos and populates relevant popular hashtags for all archive posts.
Tags are purely topic hashtags (NO body text/descriptions).
"""

import os
import json
import sqlite3
import re
from PIL import Image, ImageStat

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "crosspost.db")
MEDIA_DIR = os.path.join(SCRIPT_DIR, "docs", "media", "archive")


def analyze_image_tags(post_id: int, raw_text: str = "") -> str:
    """
    Analyzes post photos and context to generate 2-4 popular relevant hashtags.
    Returns clean hashtag string, e.g.: '#streetphotography #чб #фотография'
    """
    text_lower = (raw_text or "").lower()

    # Priority location / subject keywords from original context
    if any(w in text_lower for w in ["териберк", "мурманск", "баренцев"]):
        return "#териберка #север #путешествия #фотография"
    elif "север" in text_lower:
        return "#север #пейзаж #путешествия #фотография"
    elif any(w in text_lower for w in ["питер", "петербург", "спб"]):
        loc_tag = "#петербург"
    elif any(w in text_lower for w in ["москв", "кремл"]):
        loc_tag = "#москва"
    elif "метро" in text_lower:
        return "#метро #streetphotography #москва #фотография"
    elif any(w in text_lower for w in ["собак", "пес", "пёс"]):
        return "#собака #streetphotography #фотография"
    elif any(w in text_lower for w in ["кот", "кошк"]):
        return "#котики #streetphotography #фотография"
    else:
        loc_tag = None

    first_img = os.path.join(MEDIA_DIR, f"{post_id}_0.jpg")
    if not os.path.exists(first_img):
        return f"#streetphotography {loc_tag} #фотография" if loc_tag else "#streetphotography #фотография"

    try:
        im = Image.open(first_img).convert("RGB")
        thumb = im.resize((60, 60))
        stat = ImageStat.Stat(thumb)
        r, g, b = stat.mean[:3]
        brightness = 0.299 * r + 0.587 * g + 0.114 * b
        diff = (abs(r - g) + abs(g - b) + abs(b - r)) / 3.0

        # Black & White
        if diff < 6.0:
            if loc_tag:
                return f"#streetphotography #чб {loc_tag} #фотография"
            return "#streetphotography #чб #фотография"

        # Night scene (dark + colored lights)
        if brightness < 62.0:
            if loc_tag:
                return f"#ночь #streetphotography {loc_tag} #фотография"
            return "#ночь #streetphotography #nightphotography #фотография"

        # Nature / Greens dominant
        if g > r + 15 and g > b + 10:
            return "#природа #пейзаж #nature #фотография"

        # Sky / Water / Blue dominant
        if b > r + 20 and b > g + 10:
            return "#пейзаж #landscape #лето #фотография"

        # Sunset / Golden hour (strong reds/oranges)
        if r > b + 45 and r > g + 15:
            return "#закат #streetphotography #атмосфера #фотография"

        # Urban / Architecture / Street
        if loc_tag:
            return f"#streetphotography #архитектура {loc_tag} #фотография"
        return "#streetphotography #архитектура #фотография"

    except Exception:
        return f"#streetphotography {loc_tag} #фотография" if loc_tag else "#streetphotography #фотография"


def tag_all_archive_posts():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # Ensure tags column exists
    c.execute("PRAGMA table_info(archive_posts)")
    cols = [col[1] for col in c.fetchall()]
    if "tags" not in cols:
        c.execute("ALTER TABLE archive_posts ADD COLUMN tags TEXT")

    c.execute("SELECT post_id, text FROM archive_posts")
    posts = c.fetchall()
    print(f"Analyzing and tagging {len(posts)} archive posts...")

    tag_distribution = {}
    updates = []
    for pid, text in posts:
        tags = analyze_image_tags(pid, text)
        updates.append((tags, pid))
        tag_distribution[tags] = tag_distribution.get(tags, 0) + 1

    with conn:
        conn.executemany("UPDATE archive_posts SET tags = ? WHERE post_id = ?", updates)

    print("\n✅ Successfully updated tags for all archive posts!")
    print("\n📊 Tag distribution:")
    for tags, count in sorted(tag_distribution.items(), key=lambda x: -x[1]):
        print(f"  {tags}: {count} posts")


if __name__ == "__main__":
    tag_all_archive_posts()
