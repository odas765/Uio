import os
import asyncio
import pickle
from moviepy.editor import ImageClip, AudioFileClip
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

# =====================
# CONFIGURATION
# =====================
BOT_TOKEN = "8479816021:AAGuvc_auuT4iYFn2vle0xVk-t2bswey8k8"   # Replace this
ALLOWED_USER_ID = 616584208             # Replace with your Telegram numeric user ID
OAUTH_FILE = "client_secret.json"       # Your YouTube OAuth JSON file
TOKEN_PICKLE = "token.pickle"

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
user_sessions = {}

# =====================
# YOUTUBE AUTH (CONSOLE-BASED)
# =====================
def get_youtube_service():
    creds = None
    if os.path.exists(TOKEN_PICKLE):
        with open(TOKEN_PICKLE, "rb") as token:
            creds = pickle.load(token)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(OAUTH_FILE, SCOPES)
            auth_url, _ = flow.authorization_url(prompt='consent')
            print("\n🔗 Open this URL in your browser to authorize the app:\n")
            print(auth_url)
            code = input("\n📋 Paste the authorization code here: ")
            flow.fetch_token(code=code)
            creds = flow.credentials
        with open(TOKEN_PICKLE, "wb") as token:
            pickle.dump(creds, token)
    return build("youtube", "v3", credentials=creds)

# =====================
# TELEGRAM HANDLERS
# =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return await update.message.reply_text("🚫 You are not allowed to use this bot.")
    await update.message.reply_text("👋 Send me an *audio file* first.", parse_mode="Markdown")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    audio_file = await update.message.audio.get_file()
    audio_path = f"audio_{update.effective_user.id}.mp3"
    await audio_file.download_to_drive(audio_path)
    user_sessions[update.effective_user.id] = {"audio": audio_path}
    await update.message.reply_text("🎵 Audio received! Now send me an *image*.", parse_mode="Markdown")

async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return
    photo = update.message.photo[-1]
    img_file = await photo.get_file()
    img_path = f"image_{update.effective_user.id}.jpg"
    await img_file.download_to_drive(img_path)
    session = user_sessions.get(update.effective_user.id, {})
    session["image"] = img_path
    user_sessions[update.effective_user.id] = session
    await update.message.reply_text("🖼️ Image received! Now send me the *YouTube title*.", parse_mode="Markdown")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ALLOWED_USER_ID:
        return

    uid = update.effective_user.id
    text = update.message.text.strip()
    session = user_sessions.get(uid, {})

    if "title" not in session:
        session["title"] = text
        user_sessions[uid] = session
        await update.message.reply_text("✏️ Got the title! Now send the *description*.")
        return

    if "description" not in session:
        session["description"] = text
        user_sessions[uid] = session
        await update.message.reply_text("📝 Great! Now send *tags* (comma-separated).")
        return

    if "tags" not in session:
        session["tags"] = [t.strip() for t in text.split(",")]
        user_sessions[uid] = session

        await update.message.reply_text("🎬 Creating video, please wait...")

        video_path = f"video_{uid}.mp4"
        create_video(session["image"], session["audio"], video_path)
        youtube = get_youtube_service()

        request = youtube.videos().insert(
            part="snippet,status",
            body={
                "snippet": {
                    "title": session["title"],
                    "description": session["description"],
                    "tags": session["tags"]
                },
                "status": {"privacyStatus": "public"},
            },
            media_body=MediaFileUpload(video_path, chunksize=-1, resumable=True),
        )
        response = request.execute()
        video_id = response["id"]

        await update.message.reply_text(f"✅ Uploaded to YouTube!\n🔗 https://youtu.be/{video_id}")

        # cleanup
        for f in [session["audio"], session["image"], video_path]:
            if os.path.exists(f):
                os.remove(f)
        del user_sessions[uid]

def create_video(image_path, audio_path, output_path):
    audio_clip = AudioFileClip(audio_path)
    img_clip = ImageClip(image_path).set_duration(audio_clip.duration)
    img_clip = img_clip.resize(height=720)
    video = img_clip.set_audio(audio_clip)
    video.write_videofile(output_path, fps=24, codec="libx264", audio_codec="aac")

# =====================
# MAIN
# =====================
def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.PHOTO, handle_image))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    print("🤖 Bot is running... Press Ctrl+C to stop.")
    app.run_polling()

if __name__ == "__main__":
    main()
