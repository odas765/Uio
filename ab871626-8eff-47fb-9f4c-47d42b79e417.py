#!/usr/bin/env python3
"""
iu.py – Telegram (Telethon) bot that:
1) Handles ordinary video uploads (interactive metadata prompts)
2) Handles image+audio pairs ➜ builds a 4 K video with FFmpeg ➜ uploads
Dependencies:
  pip install telethon google-auth-oauthlib google-auth google-api-python-client
  sudo apt-get install ffmpeg      # or your distro’s ffmpeg package
Env vars required:
  API_ID & API_HASH  (https://my.telegram.org)
Place client_secret.json in the same folder.
"""
from __future__ import annotations
import asyncio, datetime as dt, json, logging, os, pathlib, subprocess, sys, uuid
from typing import Dict
from telethon import TelegramClient, events
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# ──────────────── Configuration ────────────────
API_ID  = int(os.getenv("API_ID", "10074048") or sys.exit("❌ Set API_ID"))
API_HASH = os.getenv("API_HASH", "a08b1ed3365fa3b04bcf2bcbf71aff4d") or sys.exit("❌ Set API_HASH")
SESSION_NAME = "tg_session"

CLIENT_SECRET_FILE = "client_secret.json"
TOKEN_FILE         = "token.json"
SCOPES             = ["https://www.googleapis.com/auth/youtube.upload"]

DOWNLOAD_DIR  = pathlib.Path("downloads")
OUTPUT_DIR    = pathlib.Path("renders")
DOWNLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# ────────────────────────────────────────────────

# ──────────────── YouTube auth ────────────────
def youtube_service():
    if not pathlib.Path(CLIENT_SECRET_FILE).exists():
        sys.exit("❌ client_secret.json missing.")
    if pathlib.Path(TOKEN_FILE).exists():
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    else:
        flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
        try:
            creds = flow.run_console()              # new libs
        except AttributeError:
            flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
            auth_url, _ = flow.authorization_url(prompt="consent")
            print("🔗  Open in browser & paste code:\n", auth_url)
            code = input("Code: ").strip()
            flow.fetch_token(code=code)
            creds = flow.credentials
        pathlib.Path(TOKEN_FILE).write_text(creds.to_json())
    return build("youtube", "v3", credentials=creds, cache_discovery=False)

YT = youtube_service()
# ────────────────────────────────────────────────

# ──────────────── Helpers ────────────────
async def ffmpeg_build(image: pathlib.Path, audio: pathlib.Path) -> pathlib.Path:
    """Combine one image + one audio into a 4 K MP4 using ffmpeg."""
    out = OUTPUT_DIR / f"{uuid.uuid4()}.mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-i", str(image),
        "-i", str(audio),
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-vf", "scale=3840:2160:force_original_aspect_ratio=decrease,"
               "pad=3840:2160:(ow-iw)/2:(oh-ih)/2",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        str(out)
    ]
    logging.info("FFmpeg cmd: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(*cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        logging.error("ffmpeg error: %s", err.decode()[:400])
        raise RuntimeError("FFmpeg failed")
    return out

def upload_video(path: pathlib.Path, title, description, tags, privacy) -> str:
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": "22"
        },
        "status": {"privacyStatus": privacy}
    }
    req = YT.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(path, chunksize=-1, resumable=True)
    )
    response = None
    while response is None:
        status, response = req.next_chunk()
        if status:
            logging.info("YT upload %.2f%%", status.progress() * 100)
    return f"https://youtu.be/{response['id']}"

def is_video(msg):  # helper to detect Telegram video / doc video
    return bool(msg.video or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video/")))

def is_image(msg):
    return bool(msg.photo or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith(("image/",))))

def is_audio(msg):
    return bool(msg.audio or (msg.document and msg.document.mime_type and msg.document.mime_type.startswith(("audio/", "video/mp4"))))
# ─────────────────────────────────────────

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)

# Keep per-chat state
state: Dict[int, Dict[str, pathlib.Path]] = {}  # {chat_id: {"image": path, "audio": path}}

@client.on(events.NewMessage(incoming=True))
async def handler(event: events.NewMessage.Event):
    chat_id = event.chat_id
    msg = event.message

    # IMAGE first
    if is_image(msg):
        p = await msg.download_media(file=DOWNLOAD_DIR)
        state[chat_id] = {"image": pathlib.Path(p)}
        await event.reply("🖼️ Image saved. Now send the **audio file** (MP3/FLAC)…")
        return

    # AUDIO second
    if is_audio(msg) and chat_id in state and "image" in state[chat_id] and "audio" not in state[chat_id]:
        p = await msg.download_media(file=DOWNLOAD_DIR)
        state[chat_id]["audio"] = pathlib.Path(p)
        await event.reply("🎬 Creating 4 K video from your image + audio. Please wait…")
        try:
            video_path = await ffmpeg_build(state[chat_id]["image"], state[chat_id]["audio"])
        except Exception as e:
            await event.reply(f"FFmpeg error: {e}")
            state.pop(chat_id, None)
            return
        # Store video path in state to continue interactive metadata prompts
        state[chat_id]["video"] = video_path
        await event.reply("📑 Please enter the **title** for YouTube upload:")
        return

    # NORMAL video upload
    if is_video(msg):
        vpath = await msg.download_media(file=DOWNLOAD_DIR)
        state[chat_id] = {"video": pathlib.Path(vpath)}
        await event.reply("📑 Please enter the **title** for YouTube upload:")
        return

    # Interactive metadata collection
    if chat_id in state and "video" in state[chat_id]:
        # Title missing
        if "title" not in state[chat_id]:
            state[chat_id]["title"] = msg.text.strip()
            await event.reply("📝 Now send the **description**:")
            return
        # Description missing
        if "description" not in state[chat_id]:
            state[chat_id]["description"] = msg.text.strip()
            await event.reply("🏷️ Send **tags** (comma-separated):")
            return
        # Tags missing
        if "tags" not in state[chat_id]:
            tags = [t.strip() for t in msg.text.split(",") if t.strip()]
            state[chat_id]["tags"] = tags
            await event.reply("🔒 Choose privacy: `public`, `unlisted`, or `private`:")
            return
        # Privacy missing
        if "privacy" not in state[chat_id]:
            priv = msg.text.strip().lower()
            if priv not in {"public", "unlisted", "private"}:
                priv = "unlisted"
            state[chat_id]["privacy"] = priv

            await event.reply("⏫ Uploading to YouTube… Please wait.")
            video_path = state[chat_id]["video"]
            try:
                yt_link = await asyncio.to_thread(
                    upload_video,
                    video_path,
                    state[chat_id]["title"],
                    state[chat_id]["description"],
                    state[chat_id]["tags"],
                    state[chat_id]["privacy"],
                )
                await event.reply(f"✅ Uploaded!\n🔗 {yt_link}")
            except Exception as e:
                await event.reply(f"❌ YouTube upload failed: {e}")
            finally:
                # clean up
                try: os.remove(video_path)
                except OSError: pass
                for k in ("image", "audio"):
                    if k in state[chat_id]:
                        try: os.remove(state[chat_id][k])
                        except OSError: pass
                state.pop(chat_id, None)
            return

async def main():
    await client.start()
    logging.info("Bot online. Awaiting messages…")
    await client.run_until_disconnected()

if __name__ == "__main__":
    try: asyncio.run(main())
    except KeyboardInterrupt: print("bye")
