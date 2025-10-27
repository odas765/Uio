#!/usr/bin/env python3
"""
iu.py – Telegram bot that:
1) Handles ordinary video uploads ➜ uploads to YouTube
2) Handles image+audio pairs ➜ builds a 1080p video with FFmpeg ➜ uploads to YouTube
Headless-safe OAuth (for Cloud Shell / VPS environments)
"""

import asyncio, os, sys, pathlib, uuid, logging, json
from typing import Dict
from telethon import TelegramClient, events
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ──────────────── CONFIG ────────────────
API_ID = int(os.getenv("API_ID", "15076648") or sys.exit("❌ Set API_ID"))
API_HASH = os.getenv("API_HASH", "a1aefc1ee1f17872e0347fc93d6f6c67") or sys.exit("❌ Set API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN", "8479816021:AAGuvc_auuT4iYFn2vle0xVk-t2bswey8k8")

SESSION_NAME = "tg_session"
CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE = "token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

DOWNLOAD_DIR = pathlib.Path("downloads")
OUTPUT_DIR = pathlib.Path("renders")
DOWNLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ──────────────── YOUTUBE AUTH ────────────────
def youtube_service():
    if not pathlib.Path(CLIENT_SECRET_FILE).exists():
        sys.exit("❌ client_secret.json missing. Get it from Google Cloud Console.")

    if pathlib.Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    else:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
        flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
        auth_url, _ = flow.authorization_url(prompt="consent")
        print("\n🔗 Open this URL in your browser and allow access:\n", auth_url)
        code = input("\n📋 Paste the authorization code here: ").strip()
        flow.fetch_token(code=code)
        creds = flow.credentials
        pathlib.Path(TOKEN_FILE).write_text(creds.to_json())

    return build("youtube", "v3", credentials=creds, cache_discovery=False)

YT = youtube_service()

# ──────────────── HELPERS ────────────────
async def ffmpeg_build(image: pathlib.Path, audio: pathlib.Path) -> pathlib.Path:
    """Combine image + audio into a 1080p MP4 using ffmpeg."""
    out = OUTPUT_DIR / f"{uuid.uuid4()}.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-i", str(image),
        "-i", str(audio),
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,"
               "pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(out)
    ]
    logging.info("FFmpeg cmd: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        logging.error("ffmpeg error: %s", err.decode()[:400])
        raise RuntimeError("FFmpeg failed")
    return out


def upload_video(path: pathlib.Path, title, description, tags, privacy) -> str:
    body = {
        "snippet": {"title": title, "description": description, "tags": tags, "categoryId": "22"},
        "status": {"privacyStatus": privacy},
    }
    req = YT.videos().insert(part="snippet,status", body=body, media_body=MediaFileUpload(path, chunksize=-1, resumable=True))
    response = None
    while response is None:
        status, response = req.next_chunk()
        if status:
            logging.info("YT upload %.2f%%", status.progress() * 100)
    return f"https://youtu.be/{response['id']}"


def is_video(msg):
    return bool(msg.video or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video/")))

def is_image(msg):
    return bool(msg.photo or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("image/")))

def is_audio(msg):
    return bool(msg.audio or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith(("audio/", "video/mp4"))))

# ──────────────── TELEGRAM BOT ────────────────
client = TelegramClient(SESSION_NAME, API_ID, API_HASH).start(bot_token=BOT_TOKEN)
state: Dict[int, Dict[str, pathlib.Path]] = {}  # per-chat state

@client.on(events.NewMessage(incoming=True))
async def handler(event: events.NewMessage.Event):
    chat_id = event.chat_id
    msg = event.message

    # IMAGE
    if is_image(msg):
        p = await msg.download_media(file=DOWNLOAD_DIR)
        state[chat_id] = {"image": pathlib.Path(p)}
        await event.reply("🖼️ Image saved. Now send the **audio file** (MP3/FLAC)…")
        return

    # AUDIO
    if is_audio(msg) and chat_id in state and "image" in state[chat_id] and "audio" not in state[chat_id]:
        p = await msg.download_media(file=DOWNLOAD_DIR)
        state[chat_id]["audio"] = pathlib.Path(p)
        await event.reply("🎬 Creating 1080p video from your image + audio. Please wait…")
        try:
            video_path = await ffmpeg_build(state[chat_id]["image"], state[chat_id]["audio"])
        except Exception as e:
            await event.reply(f"FFmpeg error: {e}")
            state.pop(chat_id, None)
            return
        state[chat_id]["video"] = video_path
        await event.reply("📑 Please enter the **title** for YouTube upload:")
        return

    # NORMAL VIDEO
    if is_video(msg):
        vpath = await msg.download_media(file=DOWNLOAD_DIR)
        state[chat_id] = {"video": pathlib.Path(vpath)}
        await event.reply("📑 Please enter the **title** for YouTube upload:")
        return

    # INTERACTIVE STEPS
    if chat_id in state and "video" in state[chat_id]:
        s = state[chat_id]
        if "title" not in s:
            s["title"] = msg.text.strip()
            await event.reply("📝 Now send the **description**:")
            return
        if "description" not in s:
            s["description"] = msg.text.strip()
            await event.reply("🏷️ Send **tags** (comma-separated):")
            return
        if "tags" not in s:
            tags = [t.strip() for t in msg.text.split(",") if t.strip()]
            s["tags"] = tags
            await event.reply("🔒 Choose privacy: `public`, `unlisted`, or `private`:")
            return
        if "privacy" not in s:
            priv = msg.text.strip().lower()
            if priv not in {"public", "unlisted", "private"}:
                priv = "unlisted"
            s["privacy"] = priv

            await event.reply("⏫ Uploading to YouTube… Please wait.")
            try:
                yt_link = await asyncio.to_thread(
                    upload_video,
                    s["video"],
                    s["title"],
                    s["description"],
                    s["tags"],
                    s["privacy"],
                )
                await event.reply(f"✅ Uploaded!\n🔗 {yt_link}")
            except Exception as e:
                await event.reply(f"❌ YouTube upload failed: {e}")
            finally:
                for k in ("video", "image", "audio"):
                    if k in s:
                        try: os.remove(s[k])
                        except OSError: pass
                state.pop(chat_id, None)
            return

# ──────────────── MAIN ────────────────
async def main():
    await client.start()
    logging.info("🤖 Bot is online and awaiting messages…")
    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("👋 Bye")
