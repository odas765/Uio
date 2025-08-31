import os
import tempfile
import numpy as np
import librosa
import soundfile as sf
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler

TOKEN = "YOUR_BOT_TOKEN_HERE"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎵 Send me an audio file (MP3/WAV/FLAC) and I’ll resample it to 96 kHz "
        "and add fake high frequencies beyond the original spectrum."
    )

def generate_fake_high_freq(y, harmonics=2):
    """
    Simple harmonic generation: generate fake high frequencies from existing waveform
    """
    y_harmonics = np.zeros_like(y)
    for i in range(2, harmonics + 1):
        y_harmonics += np.sin(i * np.arcsin(np.clip(y, -1, 1))) * 0.1
    return y_harmonics

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    file = None
    if update.message.audio:
        file = await update.message.audio.get_file()
    elif update.message.voice:
        file = await update.message.voice.get_file()
    elif update.message.document and update.message.document.mime_type.startswith("audio/"):
        file = await update.message.document.get_file()

    if not file:
        await update.message.reply_text("❌ Please send a valid audio file (mp3/wav/flac).")
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        input_path = os.path.join(tmpdir, "input_audio")
        output_path = os.path.join(tmpdir, "output_audio.wav")

        await file.download_to_drive(input_path)
        await update.message.reply_text("⏳ Processing... this may take a moment.")

        # Load audio with original sample rate
        y, sr = librosa.load(input_path, sr=None)

        # Resample to 96 kHz
        target_sr = 96000
        y_upsampled = librosa.resample(y, orig_sr=sr, target_sr=target_sr)

        # Generate fake high frequencies
        y_fake = generate_fake_high_freq(y_upsampled, harmonics=2)

        # Mix original upsampled audio with fake high frequencies
        y_final = np.clip(y_upsampled + y_fake, -1, 1)

        # Save output
        sf.write(output_path, y_final, target_sr)

        # Send processed audio back
        await update.message.reply_audio(open(output_path, "rb"), title="Upscaled 96 kHz with fake highs 🎵")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📌 Send me an audio file (MP3/WAV/FLAC) and I’ll resample it to 96 kHz "
        "and add fake high frequencies beyond the original spectrum."
    )

def main():
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(MessageHandler(filters.AUDIO | filters.VOICE | filters.Document.AUDIO, handle_audio))

    print("🎵 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
