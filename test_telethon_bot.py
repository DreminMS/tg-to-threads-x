import asyncio
import os
import sys
from telethon import TelegramClient
from telethon.tl.types import MessageMediaPhoto

API_ID = 2040
API_HASH = "b1844f7ea58714436577ea547e338b36"
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

async def main():
    if not BOT_TOKEN:
        print("Missing TELEGRAM_BOT_TOKEN")
        sys.exit(1)

    print("Connecting Telethon client with bot_token on cloud...")
    client = TelegramClient("bot_session", API_ID, API_HASH)
    await client.start(bot_token=BOT_TOKEN)
    me = await client.get_me()
    print("Bot authenticated:", me.username)

    try:
        channel = await client.get_entity("dremin_live")
        print("Channel title:", channel.title, "ID:", channel.id)
        
        # Test getting messages
        messages = await client.get_messages(channel, ids=[4615, 4616, 4617, 4618, 4619, 4620, 4621, 4622, 4623, 4624])
        print(f"Fetched {len(messages)} messages for post 4615 album!")
        for m in messages:
            if m and m.photo:
                # Inspect photo sizes
                print(f"Msg {m.id}: Photo has {len(m.photo.sizes)} sizes")
                for s in m.photo.sizes:
                    print(f"   Size type: {type(s).__name__}, w={getattr(s, 'w', None)}, h={getattr(s, 'h', None)}, size={getattr(s, 'size', None)}")
                # Download largest photo
                path = await client.download_media(m.photo, file=f"/tmp/hi_res_{m.id}.jpg")
                print(f"   Downloaded largest photo to {path}")
                import subprocess
                res = subprocess.run(["file", path], capture_output=True, text=True)
                print(f"   Resolution info: {res.stdout.strip()}")
    except Exception as e:
        print("Error fetching via bot Telethon:", e)
    finally:
        await client.disconnect()

if __name__ == "__main__":
    asyncio.run(main())
