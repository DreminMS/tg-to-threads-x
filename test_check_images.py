import os
import sys
import json
import urllib.request
from PIL import Image

photos = [
    "https://cdn4.telesco.pe/file/SCG8d4TvbaG33qbloBRvNPyXX9206ETjf0w1Lf809NScipdKeAeS0yvew_OkSnz704QUonGCXj7mLOvbtNbyBxvNfDrsIc6QOkFfeG03mQG1h7wp_DrnSeYsUlwDFmLiJIDzBUmH67wA-baMQRX-bPFbQImAqC1H5JwNtSiMIoQHEzfuKDdUxVTadBnzfa4OeNib-CRCYIvQLg2GbphQN9xXoNG1vUrc1nHgUm_qJ2lo6uRniGQisjWSZA9kBNKmBt62WkDmJFqHQw1K9O15sEEWfV0zQdm2eyvRi0HyaI14NyVwjL90DWcM8jr9ZKYqF43Fnqt_mll246vytNRg2g.jpg",
    "https://cdn4.telesco.pe/file/anPFceq8rGaS0bS4dU3fQp6Sz2PQe2ruYV4Q_Qqoa6eHZj3sxpyE-Y6xwqVwpVyv3OYZpjQ6LXawzFDAwnT14wVV6iUpdbtcg7KmLEHA1bl9FiDIyRtJn2j_Nu8-DJXDPadO_W9MBurv9mbwgMsUSCxmFIJSQXGp6mpGArPeJiXY367go_8K7ILs98hgLLEiRPFnbNJYe5H2wWpybJJ4v_NHOYavjf6Y9EilLbA8Q9VFAne2KZKHlMMbEdstHKNKL3aIxEJEItyZGHDFlDZ-QwfxwwUZnXWWc-O18EvbiGalAWwOtuJm20e5U969bnrTcK1qKzIspeEvo7dUrFxhbA.jpg"
]

print("--- 1. Testing telesco.pe photo resolution ---")
for i, url in enumerate(photos):
    tmp_path = f"/tmp/test_photo_{i}.jpg"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp, open(tmp_path, "wb") as f:
        f.write(resp.read())
    with Image.open(tmp_path) as img:
        print(f"Photo {i}: size={os.path.getsize(tmp_path)} bytes, dimensions={img.size}, format={img.format}")

print("\n--- 2. Testing Telegram Bot API getUpdates & getMe ---")
token = os.environ.get("TELEGRAM_BOT_TOKEN")
if token:
    try:
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getMe", timeout=10) as r:
            print("getMe:", r.read().decode())
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getChat?chat_id=@dremin_live", timeout=10) as r:
            print("getChat:", r.read().decode())
        with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates?offset=-10", timeout=10) as r:
            updates = json.loads(r.read().decode()).get("result", [])
            print(f"getUpdates count: {len(updates)}")
            for u in updates:
                msg = u.get("channel_post") or u.get("message") or {}
                print("Update ID:", u.get("update_id"), "Message ID:", msg.get("message_id"), "Has photo:", "photo" in msg)
                if "photo" in msg:
                    print("  Photo sizes:", [(p.get("width"), p.get("height"), p.get("file_size")) for p in msg["photo"]])
    except Exception as e:
        print("Bot API error:", e)
else:
    print("No TELEGRAM_BOT_TOKEN provided")
