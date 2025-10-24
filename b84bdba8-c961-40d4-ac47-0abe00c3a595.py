#!/usr/bin/env python3
"""
tg_to_youtube_bot.py

Usage:
 - Set environment variable TELEGRAM_TOKEN with your bot token.
 - Place your Google OAuth client secrets JSON (OAuth 2.0 Client ID) as "client_secrets.json"
   or set GOOGLE_CLIENT_SECRETS env var to the path.
 - First time run will open a browser window (or give a URL) to authorize YouTube upload access.
 - Then run the bot. Send a photo and an audio/voice/file to the bot; when both are present,
   it will create a 720p MP4 and upload it to your YouTube account.

Note: You must enable YouTube Data API v3 in Google Cloud Console for the OAuth client.
"""

import os
import logging
import asyncio
import tempfile
import json
import pathlib
from pathlib import Path
from typing import Optional

# Telegram
from telegram import Update, MessageEntity
from telegram.ext import ApplicationBuilder, ContextTypes, CommandHandler, MessageHandler, filters

# Google / YouTube
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

# Subprocess for ffmpeg
import subprocess

# ---------- Configuration ----------
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GOOGLE_CLIENT_SECRETS = os.environ.get("GOOGLE_CLIENT_SECRETS", "client_secrets.json")
CREDENTIALS_PATH = os.environ.get("GOOGLE_CREDENTIALS", "youtube_credentials.json")

# YouTube scopes needed for upload + manage
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube"]

# Directory to store per-user temp files
BASE_DIR = Path(tempfile.gettempdir()) / "tg_to_youtube"
BASE_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# In-memory mapping user_id -> dict with 'image_path' and 'audio_path'
user_assets = {}

# ---------- Helper functions ----------

def get_user_folder(user_id: int) -> Path:
    p = BASE_DIR / str(user_id)
    p.mkdir(parents=True, exist_ok=True)
    return p

def creds_exist() -> bool:
    return Path(CREDENTIALS_PATH).exists()

def load_credentials() -> Optional[Credentials]:
    if not creds_exist():
        return None
    with open(CREDENTIALS_PATH, "r") as f:
        data = json.load(f)
    return Credentials.from_authorized_user_info(data, scopes=YOUTUBE_SCOPES)

def save_credentials(creds: Credentials):
    with open(CREDENTIALS_PATH, "w") as f:
        f.write(creds.to_json())

def ensure_youtube_credentials():
    """
    Returns google.oauth2.credentials.Credentials authorized to use the YouTube Data API.
    On first run it will perform the installed-app OAuth flow (opens a browser or prints a URL).
    """
    creds = load_credentials()
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            save_credentials(creds)
            return creds
        except Exception as e:
            logger.warning("Failed to refresh credentials: %s", e)

    # Start installed app flow (this will prompt a browser / display URL).
    flow = InstalledAppFlow.from_client_secrets_file(GOOGLE_CLIENT_SECRETS, scopes=YOUTUBE_SCOPES)
    creds = flow.run_local_server(port=0)
    save_credentials(creds)
    return creds

def ffmpeg_create_video(image_path: Path, audio_path: Path, output_path: Path):
    """
    Uses ffmpeg to create a 1280x720 mp4 from a single image and an audio file.
    Keeps aspect ratio, pads to 1280x720, uses libx264 and aac.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-loop", "1",
        "-i", str(image_path),
        "-i", str(audio_path),
        "-c:v", "libx264",
        "-tune", "stillimage",
        "-c:a", "aac",
        "-b:a", "192k",
        "-pix_fmt", "yuv420p",
        "-shortest",
        "-vf",
        "scale=1280:720:force_original_aspect_ratio=decrease,pad=1280:720:(ow-iw)/2:(oh-ih)/2,format=yuv420p",
        str(output_path)
    ]
    logger.info("Running ffmpeg: %s", " ".join(cmd))
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        logger.error("ffmpeg failed: %s", proc.stderr.decode())
        raise RuntimeError("ffmpeg failed: " + proc.stderr.decode())
    logger.info("Created video at %s", output_path)
    return output_path

def youtube_upload_video(creds: Credentials, video_path: Path, title: str = "Uploaded from Telegram Bot", description: str = "", privacyStatus: str = "unlisted"):
    """
    Uploads video to YouTube. Returns the video id on success.
    """
    youtube = build("youtube", "v3", credentials=creds, cache_discovery=False)
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": ["telegram", "upload", "bot"]
        },
        "status": {
            "privacyStatus": privacyStatus
        }
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True, mimetype="video/*")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    logger.info("Starting upload to YouTube...")
    try:
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info("Upload progress: %.2f%%", status.progress() * 100)
    except Exception as e:
        logger.exception("Upload failed: %s", e)
        raise
    logger.info("Upload finished. Video ID: %s", response.get("id"))
    return response.get("id")

# ---------- Telegram handlers ----------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hi! Send a PHOTO and an AUDIO/VOICE/FILE (mp3/m4a/ogg) to me. "
        "When both are received I will create a 720p video and upload to YouTube (your channel)."
    )

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot running. Send /start to get instructions.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = msg.from_user.id
    folder = get_user_folder(user_id)
    photo = msg.photo[-1]  # best quality
    path = folder / "image.jpg"
    await photo.get_file().download(custom_path=str(path))
    user_assets[user_id] = user_assets.get(user_id, {})
    user_assets[user_id]["image_path"] = path
    await msg.reply_text(f"Saved image ({path.name}). Now send the audio you want to pair with it.")
    # attempt to produce if audio already present
    if "audio_path" in user_assets[user_id]:
        await process_and_upload(update, context, user_id)

async def handle_audio_or_voice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    user_id = msg.from_user.id
    folder = get_user_folder(user_id)
    # audio can be msg.audio, msg.voice, msg.document
    audio_file = None
    if msg.audio:
        audio_file = msg.audio
        filename = "audio." + (audio_file.file_name.split(".")[-1] if audio_file.file_name else "mp3")
    elif msg.voice:
        audio_file = msg.voice
        filename = "voice.ogg"  # telegram voice is typically ogg/opus
    elif msg.document:
        audio_file = msg.document
        filename = audio_file.file_name or "audiofile"
    else:
        await msg.reply_text("No audio-like file found in the message.")
        return

    target = folder / "audio_in"
    await audio_file.get_file().download(custom_path=str(target))
    # convert to mp3/aac-compatible container if necessary using ffmpeg
    converted = folder / "audio.mp3"
    try:
        cmd = ["ffmpeg", "-y", "-i", str(target), "-vn", "-acodec", "mp3", str(converted)]
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    except Exception as e:
        logger.warning("ffmpeg audio conversion failed: %s", e)
        # fallback: use the original file
        converted = target
    user_assets[user_id] = user_assets.get(user_id, {})
    user_assets[user_id]["audio_path"] = converted
    await msg.reply_text(f"Saved audio ({converted.name}). Now send the image you want to pair with it.")
    if "image_path" in user_assets[user_id]:
        await process_and_upload(update, context, user_id)

async def process_and_upload(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """
    Combine image and audio to create video and upload to YouTube.
    This function runs synchronously (blocking) because ffmpeg and upload are heavy operations.
    We run it in a threadpool to avoid blocking the event loop.
    """
    chat = update.effective_chat
    msg = update.message
    assets = user_assets.get(user_id, {})
    image_path = assets.get("image_path")
    audio_path = assets.get("audio_path")
    if not image_path or not audio_path:
        await chat.send_message("Waiting for both image and audio.")
        return

    # Prepare file paths
    folder = get_user_folder(user_id)
    output_video = folder / "out_720p.mp4"

    async def blocking_work():
        # ensure credentials
        creds = ensure_youtube_credentials()  # may open browser the first time
        # create video
        ffmpeg_create_video(image_path, audio_path, output_video)
        # upload
        title = f"Telegram upload by {user_id}"
        description = "Uploaded via Telegram bot."
        video_id = youtube_upload_video(creds, output_video, title=title, description=description)
        return video_id

    # Run the blocking_work in executor
    loop = asyncio.get_running_loop()
    try:
        await chat.send_message("Processing video and uploading to YouTube. This may take a few minutes...")
        video_id = await loop.run_in_executor(None, blocking_work)
    except Exception as e:
        logger.exception("Failed to create/upload video: %s", e)
        await chat.send_message(f"Failed: {e}")
        return

    yt_url = f"https://youtu.be/{video_id}"
    await chat.send_message(f"Upload finished: {yt_url}")

    # Optionally clear stored assets for the user
    user_assets.pop(user_id, None)

# ---------- Main run ----------

def main():
    token = TELEGRAM_TOKEN
    if not token:
        raise RuntimeError("Please set TELEGRAM_TOKEN env var to your bot token.")
    application = ApplicationBuilder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    # Accept audio, voice, and document (for audio files)
    audio_filter = filters.AUDIO | filters.VOICE | (filters.Document.ALL & filters.Document.MIME_TYPE("audio/*"))
    application.add_handler(MessageHandler(audio_filter, handle_audio_or_voice))

    logger.info("Bot started. Listening for messages...")
    application.run_polling()

if __name__ == "__main__":
    main()
