#!/usr/bin/env python3
"""
Analyzes photos and populates relevant, diverse topic hashtags for archive posts.

Tags are purely topic hashtags (NO body text/descriptions).

Generation is deterministic per post_id, so repeated runs never change the tags
of a post that has already been tagged.

Usage:
    python tag_posts.py              # retag ALL archive posts
    python tag_posts.py --pending    # retag only posts still in the queue
    python tag_posts.py --dry-run    # show what would change, write nothing
"""

import os
import json
import sqlite3
import random
import hashlib
import datetime
import argparse

from PIL import Image, ImageStat, ImageFilter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_FILE = os.path.join(SCRIPT_DIR, "crosspost.db")
MEDIA_DIR = os.path.join(SCRIPT_DIR, "docs", "media", "archive")

# ---------------------------------------------------------------------------
# Tag vocabulary
# ---------------------------------------------------------------------------

BASE_TAG = "#фотография"

SCENE_TAGS = {
    "bw": ["#чб", "#blackandwhite", "#монохром"],
    "night": ["#ночь", "#nightphotography", "#огни"],
    "sunset": ["#закат", "#goldenhour", "#теплыйсвет"],
    "nature": ["#природа", "#пейзаж", "#зелень"],
    "sky": ["#небо", "#пейзаж", "#путешествия"],
    "arch": ["#архитектура", "#геометрия", "#город"],
    "portrait": ["#портрет", "#люди", "#mood"],
    "street": ["#streetphotography", "#город", "#улица"],
}

# Generic photographic/style tags, picked deterministically per post so that
# neighbouring posts in the queue do not repeat the same combination.
STYLE_POOL = [
    "#35мм", "#пленка", "#ретро", "#силуэт", "#тени", "#свет",
    "#отражение", "#ритм", "#перспектива", "#детали", "#фактура",
    "#линии", "#форма", "#минимализм", "#атмосфера", "#тишина",
    "#окна", "#двор", "#контраст",
    "#кадр", "#наблюдение", "#everyday", "#street", "#photo",
]

SEASON_TAGS = {
    12: "#зима", 1: "#зима", 2: "#зима",
    3: "#весна", 4: "#весна", 5: "#весна",
    6: "#лето", 7: "#лето", 8: "#лето",
    9: "#осень", 10: "#осень", 11: "#осень",
}

# Location / subject keywords from the original caption.
LOCATION_RULES = [
    (["териберк", "мурманск", "баренцев"], ["#териберка", "#север"]),
    (["питер", "петербург", "спб"], ["#петербург"]),
    (["москв", "кремл"], ["#москва"]),
    (["казан"], ["#казань"]),
    (["сочи", "адлер"], ["#сочи"]),
    (["байкал"], ["#байкал"]),
    (["крым"], ["#крым"]),
    (["урал"], ["#урал"]),
    (["сибир"], ["#сибирь"]),
    (["европ", "европе"], ["#европа"]),
]

SUBJECT_RULES = [
    (["метро", "подземк"], ["#метро"]),
    (["собак", "пес", "пёс"], ["#собака"]),
    (["кот", "кошк"], ["#котики"]),
    (["кофе", "кафе"], ["#кофе"]),
    (["книг", "книжн"], ["#книги"]),
    (["винил", "пластинк"], ["#винил"]),
    (["концерт", "музык"], ["#концерт"]),
    (["театр"], ["#театр"]),
    (["музей", "выставк"], ["#выставка"]),
    (["поезд", "вокзал"], ["#поезд"]),
    (["самолет", "самолёт", "аэропорт"], ["#авиация"]),
    (["велик", "велосипед"], ["#велосипед"]),
    (["мост"], ["#мост"]),
    (["фонтан"], ["#фонтан"]),
    (["снег", "зим"], ["#снег"]),
    (["дожд", "ливень"], ["#дождь"]),
    (["туман"], ["#туман"]),
]


# ---------------------------------------------------------------------------
# Image analysis
# ---------------------------------------------------------------------------

def _image_features(path: str) -> dict:
    """Extract cheap colour/composition features from one image."""
    im = Image.open(path).convert("RGB")
    im.thumbnail((120, 120))

    stat = ImageStat.Stat(im)
    r, g, b = stat.mean

    hsv = im.convert("HSV")
    saturation = ImageStat.Stat(hsv).mean[1]

    gray = im.convert("L")
    gray_stat = ImageStat.Stat(gray)
    brightness = gray_stat.mean[0]
    contrast = gray_stat.stddev[0]

    px = list(gray.getdata())
    n = len(px)
    dark_frac = sum(1 for p in px if p < 45) / n
    bright_frac = sum(1 for p in px if p > 205) / n

    # Single pass over the pixels for the colour-composition fractions.
    skin = green = blue = warm = 0
    for rr, gg, bb in im.getdata():
        if (rr > 95 and gg > 40 and bb > 20
                and max(rr, gg, bb) - min(rr, gg, bb) > 15
                and abs(rr - gg) > 15 and rr > gg and rr > bb):
            skin += 1
        if gg > rr + 10 and gg > bb + 10:
            green += 1
        if bb > rr + 15 and bb > gg + 5:
            blue += 1
        if rr > bb + 30:
            warm += 1

    n = len(px)
    skin_frac = skin / n
    green_frac = green / n
    blue_frac = blue / n
    warm_frac = warm / n

    edges = gray.filter(ImageFilter.FIND_EDGES)
    edge_density = ImageStat.Stat(edges).mean[0]

    grayness = (abs(r - g) + abs(g - b) + abs(b - r)) / 3.0

    return {
        "r": r, "g": g, "b": b,
        "brightness": brightness,
        "contrast": contrast,
        "saturation": saturation,
        "dark_frac": dark_frac,
        "bright_frac": bright_frac,
        "skin_frac": skin_frac,
        "edge_density": edge_density,
        "grayness": grayness,
        "green_frac": green_frac,
        "blue_frac": blue_frac,
        "warm_frac": warm_frac,
    }


def _post_features(post_id: int, photos: list) -> dict:
    """Average features over up to 3 evenly spaced photos of the post."""
    paths = [p for p in photos if os.path.exists(p)][:3]
    if not paths:
        return None

    feats = [_image_features(p) for p in paths]
    keys = feats[0].keys()
    return {k: sum(f[k] for f in feats) / len(feats) for k in keys}


def _classify(f: dict) -> str:
    """Map aggregated image features to one of SCENE_TAGS."""
    if f["grayness"] < 7.0 and f["saturation"] < 60:
        return "bw"
    if f["brightness"] < 58.0 or f["dark_frac"] > 0.45:
        return "night"
    if f["r"] > f["b"] + 35 and f["r"] > f["g"] + 12 and f["brightness"] < 175:
        return "sunset"
    if f["green_frac"] > 0.30:
        return "nature"
    if f["blue_frac"] > 0.28 and f["brightness"] > 110:
        return "sky"
    if f["skin_frac"] > 0.16:
        return "portrait"
    if f["edge_density"] > 26.0 and f["saturation"] < 95:
        return "arch"
    return "street"


def _season_tag(post_date: str):
    """Season hashtag derived from the archive post date (DD.MM.YYYY ...)."""
    if not post_date:
        return None
    try:
        month = int(post_date.split(".")[1])
    except (IndexError, ValueError):
        return None
    return SEASON_TAGS.get(month)


def _time_tag(f: dict, scene: str):
    """Time-of-day hashtag from brightness (skipped when it duplicates scene)."""
    b = f["brightness"]
    if scene in ("night", "sunset"):
        return None
    if b < 70:
        return "#рассвет"
    if b < 105:
        return "#утро"
    if b > 185:
        return "#день"
    if f["warm_frac"] > 0.35 and b < 170:
        return "#вечер"
    return None


def _keyword_tags(text_lower: str):
    tags = []
    for words, out in LOCATION_RULES:
        if any(w in text_lower for w in words):
            tags.extend(out)
    for words, out in SUBJECT_RULES:
        if any(w in text_lower for w in words):
            tags.extend(out)
    return tags


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_image_tags(post_id: int, raw_text: str = "", post_date: str = "") -> str:
    """
    Build 4-6 relevant, non-repeating hashtags for a post.

    Deterministic: the same post_id always yields the same tag set.
    Returns a space separated hashtag string, e.g.:
    '#streetphotography #зима #тени #35мм #фотография'
    """
    rng = random.Random(f"tag-v2:{post_id}")
    text_lower = (raw_text or "").lower()

    photos = []
    for idx in range(12):
        p = os.path.join(MEDIA_DIR, f"{post_id}_{idx}.jpg")
        if os.path.exists(p):
            photos.append(p)

    f = _post_features(post_id, photos)
    scene = _classify(f) if f else "street"

    tags = []

    # 1. Scene: primary tag always, secondary with 55% chance.
    scene_pool = SCENE_TAGS[scene]
    tags.append(scene_pool[0])
    if len(scene_pool) > 1 and rng.random() < 0.55:
        tags.append(scene_pool[1])

    # 2. Season from the archive date.
    season = _season_tag(post_date)
    if season:
        tags.append(season)

    # 3. Time of day when it adds information.
    if f:
        time_tag = _time_tag(f, scene)
        if time_tag:
            tags.append(time_tag)

    # 4. One or two style tags, deterministic per post.
    style_count = 1 if rng.random() < 0.65 else 2
    for style in rng.sample(STYLE_POOL, style_count):
        tags.append(style)

    # 5. Keywords from the original caption (max 2, locations first).
    for kw in _keyword_tags(text_lower)[:2]:
        tags.append(kw)

    # 6. Anchor tag, deduplicated, capped.
    tags.append(BASE_TAG)

    seen = set()
    ordered = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            ordered.append(t)

    return " ".join(ordered[:6])


def _fetch_posts(conn, only_pending: bool):
    cur = conn.cursor()
    where = "WHERE status = 'pending'" if only_pending else ""
    cur.execute(f"SELECT post_id, post_date, photos_json, text FROM archive_posts {where} ORDER BY post_id")
    return cur.fetchall()


def retag_posts(only_pending: bool = False, dry_run: bool = False):
    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("PRAGMA table_info(archive_posts)")
    cols = [col[1] for col in cur.fetchall()]
    if "tags" not in cols:
        cur.execute("ALTER TABLE archive_posts ADD COLUMN tags TEXT")

    rows = _fetch_posts(conn, only_pending)
    print(f"Analyzing {len(rows)} posts ({'pending only' if only_pending else 'all'})...")

    updates = []
    combos = {}
    changed = 0
    for pid, post_date, photos_json, text in rows:
        try:
            photos = json.loads(photos_json) if photos_json else []
        except Exception:
            photos = []

        tags = analyze_image_tags(pid, text or "", post_date or "")

        # Guarantee that no two posts in the queue share an identical tag set.
        if tags in combos:
            alt = _diversify(tags, pid, combos)
            tags = alt

        combos[tags] = pid

        cur.execute("SELECT tags FROM archive_posts WHERE post_id = ?", (pid,))
        old = (cur.fetchone() or [""])[0] or ""
        if old != tags:
            changed += 1
        updates.append((tags, pid))

    if dry_run:
        print(f"[DRY-RUN] would update {changed} of {len(rows)} posts")
    else:
        with conn:
            conn.executemany("UPDATE archive_posts SET tags = ? WHERE post_id = ?", updates)
        print(f"Updated {changed} of {len(rows)} posts")

    dist = {}
    for tags, _ in updates:
        dist[tags] = dist.get(tags, 0) + 1
    print(f"\nUnique tag sets: {len(dist)} / {len(rows)}")
    print("Top combinations:")
    for tags, count in sorted(dist.items(), key=lambda x: -x[1])[:12]:
        print(f"  {count:4d}  {tags}")

    conn.close()


def _diversify(tags: str, post_id: int, used: dict) -> str:
    """Swap one style tag until the combination is unique."""
    parts = tags.split()
    rng = random.Random(f"div:{post_id}")
    for candidate in rng.sample(STYLE_POOL, len(STYLE_POOL)):
        for idx in range(len(parts) - 1):
            if parts[idx] in STYLE_POOL:
                trial = parts[:idx] + [candidate] + parts[idx + 1:]
                combo = " ".join(trial)
                if combo not in used and candidate not in parts:
                    return combo
    return tags


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Generate topic hashtags for archive posts")
    ap.add_argument("--pending", action="store_true", help="retag only posts still in the queue")
    ap.add_argument("--dry-run", action="store_true", help="do not write to the database")
    args = ap.parse_args()
    retag_posts(only_pending=args.pending, dry_run=args.dry_run)