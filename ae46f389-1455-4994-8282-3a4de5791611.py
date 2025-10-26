import subprocess
import shutil
import uuid
from pathlib import Path
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ================= CONFIG =================
BOT_TOKEN = "8479816021:AAGuvc_auuT4iYFn2vle0xVk-t2bswey8k8"
BASE_DOWNLOAD_DIR = Path.home() / "Apple Music"  # Root folder for all downloads
EXTENSIONS = [".m4a", ".mp3", ".flac", ".lrc"]  # File types to send

# ================= BOT HANDLERS =================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Hello! Send me an Apple Music link, and I will download it and send it back automatically."
    )

async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat.id
    link = update.message.text.strip()
    await update.message.reply_text(f"Received link. Starting download for: {link}")

    # --- Create a unique folder for this request ---
    unique_id = uuid.uuid4().hex[:8]
    user_folder = BASE_DOWNLOAD_DIR / f"user_{chat_id}_{unique_id}"
    user_folder.mkdir(parents=True, exist_ok=True)

    # --- Run Gamdl CLI ---
    cmd = [
        "gamdl",
        "--codec-song", "aac",       # Change codec if needed
        "--output-path", str(user_folder),
        link
    ]

    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        await update.message.reply_text("❌ Download failed. Check the link or your setup.")
        shutil.rmtree(user_folder, ignore_errors=True)
        return

    # --- Send downloaded files to the user ---
    files_sent = 0
    for file_path in user_folder.rglob("*"):
        if file_path.suffix.lower() in EXTENSIONS:
            try:
                with open(file_path, "rb") as f:
                    await context.bot.send_document(chat_id=chat_id, document=f, filename=file_path.name)
                files_sent += 1
            except Exception as e:
                await update.message.reply_text(f"Failed to send {file_path.name}: {e}")

    # --- Delete the user folder to save space ---
    shutil.rmtree(user_folder, ignore_errors=True)

    if files_sent:
        await update.message.reply_text(f"✅ Sent {files_sent} files.")
    else:
        await update.message.reply_text("No files found to send.")

# ================= MAIN =================
if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_link))

    print("Bot running... Users can send Apple Music links.")
    app.run_polling()
