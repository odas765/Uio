import os
import re
import shutil
import asyncio
import logging
import mutagen
from telethon import TelegramClient, events
from telethon.tl.types import DocumentAttributeAudio

# --- CONFIG ---
API_ID = 8349121  # 🔹 your Telegram API ID
API_HASH = "9709d9b8c6c1aa3dd50107f97bb9aba6"  # 🔹 your Telegram API hash
BOT_TOKEN = "8479816021:AAGuvc_auuT4iYFn2vle0xVk-t2bswey8k8"  # 🔹 your bot token

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
        "`/download <beatport-or-beatsource-link>` – Download album + send cover card with FLACs 💫"
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

        # --- Find latest downloaded folder ---
        subfolders = [os.path.join(DOWNLOADS_DIR, d) for d in os.listdir(DOWNLOADS_DIR)]
        if not subfolders:
            await event.reply("⚠️ No downloads found.")
            return

        release_path = max(subfolders, key=os.path.getmtime)

        # --- Find cover image ---
        cover_path = None
        for fname in ["cover.jpg", "folder.jpg", "front.jpg", "cover.png"]:
            test_path = os.path.join(release_path, fname)
            if os.path.exists(test_path):
                cover_path = test_path
                break

        # --- Extract metadata ---
        album_title = os.path.basename(release_path)
        all_artists = set()
        catalog_number = "Unknown"
        tracklist = []

        for root, dirs, files in os.walk(release_path):
            for f in files:
                if f.endswith(".flac"):
                    file_path = os.path.join(root, f)
                    audio = mutagen.File(file_path)
                    title = os.path.splitext(f)[0]

                    if audio is not None and hasattr(audio, "tags") and audio.tags is not None:
                        if "TPE1" in audio.tags:
                            all_artists.add(str(audio.tags["TPE1"]))
                        elif "artist" in audio.tags:
                            all_artists.add(str(audio.tags["artist"][0]))

                        if "TALB" in audio.tags:
                            album_title = str(audio.tags["TALB"])
                        elif "album" in audio.tags:
                            album_title = str(audio.tags["album"][0])

                        if "TPUB" in audio.tags:
                            catalog_number = str(audio.tags["TPUB"])
                        elif "CATALOGNUMBER" in audio.tags:
                            catalog_number = str(audio.tags["CATALOGNUMBER"][0])

                        if "TIT2" in audio.tags:
                            title = str(audio.tags["TIT2"])
                        elif "title" in audio.tags:
                            title = str(audio.tags["title"][0])

                    tracklist.append(f"• {title}")

        if not all_artists and " - " in album_title:
            all_artists.add(album_title.split(" - ")[0])

        artists_str = ", ".join(sorted(all_artists)) or "Unknown Artist"
        tracklist_str = "\n".join(tracklist[:15]) if tracklist else "No tracklist found."

        # --- Send album caption card ---
        caption = (
            f"🎵 *{album_title}*\n"
            f"👨‍🎤 *Artists:* {artists_str}\n"
            f"🆔 *Catalog:* `{catalog_number}`\n\n"
            f"🎶 *Tracklist:*\n{tracklist_str}\n\n"
            f"🌐 [View on Beatport]({input_text})"
        )

        if cover_path:
            await bot.send_file(
                event.chat_id,
                file=cover_path,
                caption=caption,
                parse_mode="markdown"
            )
        else:
            await event.reply(caption, parse_mode="markdown")

        # --- Send only FLAC tracks ---
        sent_files = 0
        for root, dirs, files in os.walk(release_path):
            for f in files:
                if f.endswith(".flac"):
                    file_path = os.path.join(root, f)
                    try:
                        audio = mutagen.File(file_path)
                        duration = 0
                        title = os.path.splitext(f)[0]
                        artist = "Unknown Artist"

                        if audio is not None:
                            if hasattr(audio, "info") and getattr(audio.info, "length", None):
                                duration = int(audio.info.length)
                            if hasattr(audio, "tags") and audio.tags is not None:
                                if "TIT2" in audio.tags:
                                    title = str(audio.tags["TIT2"])
                                elif "title" in audio.tags:
                                    title = str(audio.tags["title"][0])

                                if "TPE1" in audio.tags:
                                    artist = str(audio.tags["TPE1"])
                                elif "artist" in audio.tags:
                                    artist = str(audio.tags["artist"][0])

                        await bot.send_file(
                            event.chat_id,
                            file=file_path,
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
                        await event.reply(f"⚠️ Couldn't send {f}: {e}")

        shutil.rmtree(release_path, ignore_errors=True)

        if sent_files > 0:
            await event.reply(f"✅ Sent {sent_files} FLAC file(s) successfully.")
        else:
            await event.reply("⚠️ No FLAC files found to send.")

    except asyncio.TimeoutError:
        await event.reply("⏱️ CLI download took too long and was stopped.")
        process.kill()
    except Exception as e:
        await event.reply(f"⚠️ Error: {e}")


# --- MAIN ---
def main():
    print("🤖 Bot is online... waiting for commands.")
    bot.run_until_disconnected()


if __name__ == "__main__":
    main()
