import os
import re
import shutil
import subprocess
import asyncio
import logging
import mutagen
from telethon import TelegramClient, events
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
bot = TelegramClient("beatport_bot", API_ID, API_HASH).start(bot_token=BOT_TOKEN)

# --- REGEX PATTERN ---
pattern = r"^https:\/\/www\.(beatport|beatsource)\.com\/(track|release)\/[\w\-\+]+\/\d+$"


# --- START / HELP ---
@bot.on(events.NewMessage(pattern=r'^/(start|help)$'))
async def start_handler(event):
    text = (
        "🤖 *Hey there!* I'm your Beatport + Beatsource Downloader Bot ⚡\n"
        "Developed by @piklujazz\n\n"
        "🗣️ Commands:\n"
        "`/download <beatport-or-beatsource-link>` – Download any Beatport or Beatsource track or album 💫"
    )
    await event.reply(text, parse_mode="markdown")


# --- DOWNLOAD COMMAND ---
@bot.on(events.NewMessage(pattern=r'^/download\s+(.+)$'))
async def download_handler(event):
    input_text = event.pattern_match.group(1).strip()

    if not re.match(pattern, input_text):
        await event.reply(
            "❌ Invalid link.\nPlease send a valid *Beatport* or *Beatsource* track or release link.",
            parse_mode="markdown"
        )
        return

    await event.reply("⚙️ Downloading... please wait ⏳")

    try:
        # --- Run BeatportDL CLI ---
        process = await asyncio.create_subprocess_exec(
            "go", "run", "./cmd/beatportdl", input_text,
            cwd=BEATPORTDL_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            stdin=asyncio.subprocess.DEVNULL
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=600)
        logger.info(stdout.decode())
        logger.error(stderr.decode())

        release_id = input_text.rstrip("/").split("/")[-1]
        release_path = os.path.join(DOWNLOADS_DIR, release_id)

        if not os.path.exists(release_path):
            await event.reply("⚠️ Download folder not found. Maybe the CLI didn’t create it.")
            return

        # --- Extract caption info from first audio file ---
        artists = "Unknown"
        title = "Unknown"
        catalogue = "Unknown"

        first_audio = None
        for root, _, files in os.walk(release_path):
            for f in files:
                if f.endswith(('.flac', '.mp3')):
                    first_audio = os.path.join(root, f)
                    break
            if first_audio:
                break

        if first_audio:
            try:
                audio = mutagen.File(first_audio, easy=True)
                if audio:
                    title = audio.get("album", ["Unknown"])[0]
                    artists = ", ".join(audio.get("artist", ["Unknown"]))
                    catalogue = audio.get("catalogue_number", ["Unknown"])[0]
            except Exception as e:
                logger.warning(f"Metadata read error: {e}")

        caption = (
            f"🎨 *Artists:* {artists}\n"
            f"💽 *Title:* {title}\n"
            f"🧾 *Catalogue:* {catalogue}"
        )

        # --- Send cover image with caption if exists ---
        cover_path = os.path.join(release_path, "cover.jpg")
        if os.path.exists(cover_path):
            await bot.send_file(event.chat_id, file=cover_path, caption=caption, parse_mode="markdown")
        else:
            await bot.send_message(event.chat_id, caption, parse_mode="markdown")

        # --- Send all audio files ---
        sent_files = 0
        for root, dirs, files in os.walk(release_path):
            for f in files:
                if f.endswith(('.flac', '.mp3')):
                    file_path = os.path.join(root, f)
                    try:
                        audio = mutagen.File(file_path)
                        duration = 0
                        title_tag = os.path.splitext(f)[0]
                        artist_tag = "Unknown Artist"

                        if audio is not None:
                            if hasattr(audio, "info") and getattr(audio.info, "length", None):
                                duration = int(audio.info.length)

                            if hasattr(audio, "tags") and audio.tags is not None:
                                if "TIT2" in audio.tags:
                                    title_tag = str(audio.tags["TIT2"])
                                elif "title" in audio.tags:
                                    title_tag = str(audio.tags["title"][0])
                                if "TPE1" in audio.tags:
                                    artist_tag = str(audio.tags["TPE1"])
                                elif "artist" in audio.tags:
                                    artist_tag = str(audio.tags["artist"][0])

                        if artist_tag == "Unknown Artist":
                            parts = os.path.splitext(f)[0].replace("_", " ").split(" - ")
                            if len(parts) >= 2:
                                artist_tag, title_tag = parts[0].strip(), parts[1].strip()

                        await bot.send_file(
                            event.chat_id,
                            file=file_path,
                            attributes=[
                                DocumentAttributeAudio(
                                    duration=duration,
                                    title=title_tag,
                                    performer=artist_tag
                                )
                            ]
                        )
                        sent_files += 1

                    except Exception as e:
                        await event.reply(f"⚠️ Couldn't send {f}: {e}")

        shutil.rmtree(release_path, ignore_errors=True)

        if sent_files > 0:
            await event.reply(f"✅ Sent {sent_files} file(s) and cleaned up successfully.")
        else:
            await event.reply("⚠️ No audio files found to send.")

    except asyncio.TimeoutError:
        await event.reply("⏱️ CLI download took too long and was stopped.")
        process.kill()
    except Exception as e:
        await event.reply(f"⚠️ Error: {e}")


def main():
    print("🤖 Bot is online... waiting for commands.")
    bot.run_until_disconnected()


if __name__ == "__main__":
    main()
