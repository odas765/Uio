import os
import re
import shutil
import asyncio
import logging
import subprocess
import mutagen
import uuid
from telethon import TelegramClient, events, Button
from telethon.tl.types import DocumentAttributeAudio

# --- CONFIG ---
API_ID = 8349121
API_HASH = "9709d9b8c6c1aa3dd50107f97bb9aba6"
BOT_TOKEN = "8479816021:AAGuvc_auuT4iYFn2vle0xVk-t2bswey8k8"

BEATPORTDL_DIR = "/home/mostlyfx7/beatportdl"
DOWNLOADS_DIR = os.path.join(BEATPORTDL_DIR, "downloads")

# --- LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- TELETHON CLIENT ---
bot = TelegramClient("beatsource_bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# --- URL PATTERN ---
pattern = r"^https:\/\/www\.(beatport|beatsource)\.com\/(track|release)\/[\w\-\+]+\/(\d+)$"

# --- Temporary format storage (for callback) ---
download_sessions = {}  # {token: {"url": ..., "user": ...}}


# --- START / HELP ---
@bot.on(events.NewMessage(pattern=r'^/(start|help)$'))
async def start_handler(event):
    text = (
        "🤖 *Hey there!* I'm your Beatport + Beatsource Downloader Bot ⚡\n"
        "Developed by @piklujazz\n\n"
        "🗣️ Commands:\n"
        "`/download <beatport-or-beatsource-link>` – Download any Beatport or Beatsource track or release 💫"
    )
    await event.reply(text, parse_mode="markdown")


# --- DOWNLOAD COMMAND ---
@bot.on(events.NewMessage(pattern=r'^/download\s+(.+)$'))
async def download_handler(event):
    url = event.pattern_match.group(1).strip()

    match = re.match(pattern, url)
    if not match:
        await event.reply("❌ Invalid link.\nPlease send a valid Beatport or Beatsource *track* or *release* link.", parse_mode="markdown")
        return

    # Create a short unique ID for callback
    token = uuid.uuid4().hex[:8]
    download_sessions[token] = {"url": url, "user": event.sender_id}

    await event.reply(
        "🎧 Please choose your preferred format:",
        buttons=[
            [Button.inline("🎵 MP3", data=f"mp3|{token}")],
            [Button.inline("💽 FLAC", data=f"flac|{token}")],
            [Button.inline("🎶 WAV", data=f"wav|{token}")]
        ]
    )


# --- CONVERSION FUNCTION ---
async def convert_audio(input_file, output_file, fmt):
    if fmt == "flac":
        shutil.copy2(input_file, output_file)
        return True

    cmd = [
        "ffmpeg", "-y", "-i", input_file,
        "-vn", "-ar", "44100", "-ac", "2",
        "-b:a", "320k" if fmt == "mp3" else "1411k",
        output_file
    ]
    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    await process.communicate()
    return process.returncode == 0


# --- CALLBACK HANDLER ---
@bot.on(events.CallbackQuery)
async def callback_handler(event):
    try:
        data = event.data.decode()
        fmt, token = data.split("|", 1)
    except Exception:
        await event.answer("⚠️ Invalid selection.")
        return

    # Lookup the stored URL using token
    if token not in download_sessions:
        await event.edit("⚠️ Session expired or invalid.")
        return

    session = download_sessions[token]
    url = session["url"]
    user = session["user"]

    if event.sender_id != user:
        await event.answer("⚠️ You can only use your own buttons.")
        return

    match = re.match(pattern, url)
    if not match:
        await event.edit("❌ Invalid URL pattern.")
        return

    site, content_type, content_id = match.groups()
    release_path = os.path.join(DOWNLOADS_DIR, content_id)

    await event.edit(f"⚙️ Downloading original FLAC files... please wait ⏳")

    try:
        # --- Run BeatportDL CLI (downloads raw FLAC) ---
        process = await asyncio.create_subprocess_exec(
            "go", "run", "./cmd/beatportdl", url,
            cwd=BEATPORTDL_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=900)
        logger.info(stdout.decode())
        logger.error(stderr.decode())

        if not os.path.exists(release_path):
            await event.edit("⚠️ Download folder not found. Maybe the CLI didn’t create it.")
            return

        await event.edit(f"🎧 Converting to *{fmt.upper()}* format...", parse_mode="markdown")

        sent_files = 0
        converted_dir = os.path.join(release_path, f"converted_{fmt}")
        os.makedirs(converted_dir, exist_ok=True)

        for root, dirs, files in os.walk(release_path):
            for f in files:
                if f.endswith(".flac"):
                    input_file = os.path.join(root, f)
                    output_file = os.path.join(converted_dir, os.path.splitext(f)[0] + f".{fmt}")

                    success = await convert_audio(input_file, output_file, fmt)
                    if not success:
                        await event.respond(f"⚠️ Failed to convert {f}")
                        continue

                    try:
                        audio = mutagen.File(output_file)
                        duration = 0
                        title = os.path.splitext(f)[0]
                        artist = "Unknown Artist"

                        if audio is not None:
                            if hasattr(audio, "info") and getattr(audio.info, "length", None):
                                duration = int(audio.info.length)
                            if hasattr(audio, "tags") and audio.tags is not None:
                                if "TIT2" in audio.tags:
                                    title = str(audio.tags["TIT2"])
                                if "TPE1" in audio.tags:
                                    artist = str(audio.tags["TPE1"])

                        await bot.send_file(
                            event.chat_id,
                            file=output_file,
                            attributes=[
                                DocumentAttributeAudio(
                                    duration=duration,
                                    title=title,
                                    performer=artist
                                )
                            ]
                        )
                        sent_files += 1

                    except Exception as e:
                        await event.respond(f"⚠️ Couldn't send {f}: {e}")

        shutil.rmtree(release_path, ignore_errors=True)

        if sent_files > 0:
            await event.respond(f"✅ Sent {sent_files} *{fmt.upper()}* file(s) and cleaned up successfully.", parse_mode="markdown")
        else:
            await event.respond("⚠️ No converted audio files found to send.")

        # Clean up session
        del download_sessions[token]

    except asyncio.TimeoutError:
        await event.respond("⏱️ CLI download took too long and was stopped.")
        process.kill()
    except Exception as e:
        await event.respond(f"⚠️ Error: {e}")


# --- MAIN ---
def main():
    print("🤖 Bot is online... waiting for commands.")
    bot.run_until_disconnected()


if __name__ == "__main__":
    main()
