import os
import json
import logging
from moviepy.editor import ImageClip, AudioFileClip
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
)
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials

# ───────────────────────────────
# CONFIG
# ───────────────────────────────
TELEGRAM_BOT_TOKEN = "8479816021:AAGuvc_auuT4iYFn2vle0xVk-t2bswey8k8"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

# Enable logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ───────────────────────────────
# YOUTUBE AUTH
# ───────────────────────────────
def get_youtube_service():
    creds = None
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    else:
        flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
        creds = flow.run_console()  # manual link-copy method for VPS
        with open("token.json", "w") as token:
            token.write(creds.to_json())

    return build("youtube", "v3", credentials=creds)

# ───────────────────────────────
# VIDEO CREATION
# ───────────────────────────────
def create_video(image_path, audio_path, output_path):
    audio = AudioFileClip(audio_path)
    img = ImageClip(image_path).set_duration(audio.duration)
    img = img.resize(height=720)
    video = img.set_audio(audio)
    video.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac")

# ───────────────────────────────
# TELEGRAM BOT HANDLERS
# ───────────────────────────────
user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🎬 Send me an image first.")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    photo = await update.message.photo[-1].get_file()
    image_path = f"{user_id}_image.jpg"
    await photo.download_to_drive(image_path)
    user_sessions[user_id] = {"image": image_path}
    await update.message.reply_text("✅ Image saved! Now send an audio file (MP3/WAV).")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in user_sessions or "image" not in user_sessions[user_id]:
        await update.message.reply_text("Please send an image first.")
        return

    audio = await update.message.audio.get_file()
    audio_path = f"{user_id}_audio.mp3"
    await audio.download_to_drive(audio_path)

    user_sessions[user_id]["audio"] = audio_path
    await update.message.reply_text("✅ Audio received! Now send title, description, and tags (comma-separated) in one message.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in user_sessions or "audio" not in user_sessions[user_id]:
        await update.message.reply_text("Please send image and audio first.")
        return

    try:
        # Parse text input
        text_parts = update.message.text.split("\n", 2)
        title = text_parts[0].strip()
        description = text_parts[1].strip() if len(text_parts) > 1 else ""
        tags = text_parts[2].split(",") if len(text_parts) > 2 else []

        session = user_sessions[user_id]
        video_path = f"{user_id}_final.mp4"

        await update.message.reply_text("🎥 Creating video, please wait...")
        create_video(session["image"], session["audio"], video_path)

        await update.message.reply_text("⬆️ Uploading to YouTube...")
        youtube = get_youtube_service()

        body = {
            "snippet": {"title": title, "description": description, "tags": tags},
            "status": {"privacyStatus": "public"}
        }

        media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/*")
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
        response = request.execute()

        video_url = f"https://www.youtube.com/watch?v={response['id']}"
        await update.message.reply_text(f"✅ Uploaded successfully!\n{video_url}")

        # cleanup
        for f in [session["image"], session["audio"], video_path]:
            if os.path.exists(f): os.remove(f)
        del user_sessions[user_id]

    except Exception as e:
        logger.exception(e)
        await update.message.reply_text(f"❌ Error: {e}")

# ───────────────────────────────
# MAIN
# ───────────────────────────────
def main():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("🤖 Bot is running... Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
