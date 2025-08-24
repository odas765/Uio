import os
import re
import shutil
import subprocess
import json
from datetime import datetime, timedelta
from urllib.parse import urlparse
from telethon import TelegramClient, events, Button
from mutagen import File
import asyncio
from collections import deque



api_id = '10074048'
api_hash = 'a08b1ed3365fa3b04bcf2bcbf71aff4d'
session_name = 'beatport_downloader'

beatport_track_pattern = r'^https:\/\/www\.beatport\.com\/track\/[\w\-]+\/\d+$'
beatport_album_pattern = r'^https:\/\/www\.beatport\.com\/release\/[\w\-]+\/\d+$'

state = {}
ADMIN_IDS = [616584208, 731116951, 769363217]
PAYMENT_URL = "https://ko-fi.com/zackant"
USERS_FILE = 'users.json'

download_queue = deque()
is_downloading = False
queue_lock = asyncio.Lock()

async def enqueue_download(chat_id, url, content_type, format_choice):
    """
    Add a download request to the queue.
    """
    global download_queue
    download_queue.append((chat_id, url, content_type, format_choice))
    async with queue_lock:
        await process_queue()


async def process_queue():
    """
    Process the download queue sequentially (for Orpheus safety).
    Conversion + sending runs in background.
    """
    global is_downloading
    while download_queue:
        if is_downloading:
            break  # Already processing one download, wait
        is_downloading = True
        chat_id, url, content_type, format_choice = download_queue.popleft()
        try:
            await client.send_message(chat_id, "🎶 Your download has started, please wait...")
            await run_orpheus(url, content_type, format_choice, chat_id)
        except Exception as e:
            await client.send_message(chat_id, f"❌ Error during download: {e}")
        finally:
            is_downloading = False


async def run_orpheus(url, content_type, format_choice, chat_id):
    """
    Run Orpheus asynchronously and schedule conversion/sending in background.
    """
    process = await asyncio.create_subprocess_exec(
        'python', 'orpheus.py', url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise Exception(stderr.decode())

    # Notify user
    await client.send_message(chat_id, f"✅ Download finished! Converting & sending in background...")

    # 🔑 Conversion & sending runs in background (does not block the queue)
    asyncio.create_task(convert_and_send(chat_id, url, content_type, format_choice))

    # Immediately continue queue (next user download can start now)
    asyncio.create_task(process_queue())


async def convert_and_send(chat_id, input_text, content_type, format_choice):
    """
    Convert downloaded files and send to the user (runs in background).
    """
    url = urlparse(input_text)
    components = url.path.split('/')
    release_id = components[-1]

    try:
        if content_type == "album":
            root_path = f'downloads/{release_id}'
            flac_files = [f for f in os.listdir(root_path) if f.lower().endswith('.flac')]
            album_path = root_path if flac_files else os.path.join(root_path, os.listdir(root_path)[0])
            files = os.listdir(album_path)

            all_artists = set()
            catalog_number = 'N/A'
            for f in files:
                if f.lower().endswith('.flac'):
                    audio = File(os.path.join(album_path, f), easy=True)
                    if audio:
                        for key in ('artist', 'performer', 'albumartist'):
                            if key in audio:
                                all_artists.update(audio[key])
                        if 'catalog' in audio:
                            catalog_number = audio['catalog'][0]

            sample_file = next((f for f in files if f.lower().endswith('.flac')), None)
            sample_path = os.path.join(album_path, sample_file) if sample_file else None
            metadata = File(sample_path, easy=True) if sample_path else {}

            album = metadata.get('album', ['Unknown Album'])[0]
            genre = metadata.get('genre', ['Unknown Genre'])[0]
            bpm = metadata.get('bpm', ['--'])[0]
            label = metadata.get('label', ['--'])[0]
            date = metadata.get('date', ['--'])[0]
            artists_str = ", ".join(sorted(all_artists))

            caption = (
                f"<b>\U0001F3B6 Album:</b> {album}\n"
                f"<b>\U0001F464 Artists:</b> {artists_str}\n"
                f"<b>\U0001F3A7 Genre:</b> {genre}\n"
                f"<b>\U0001F4BF Label:</b> {label}\n"
                f"<b>\U0001F4C5 Release Date:</b> {date}\n"
                f"<b>\U0001F9E9 BPM:</b> {bpm}\n"
            )

            cover_file = next((os.path.join(album_path, f) for f in files if f.lower().startswith('cover') and f.lower().endswith(('.jpg', '.jpeg', '.png'))), None)
            if cover_file:
                await client.send_file(chat_id, cover_file, caption=caption, parse_mode='html')
            else:
                await client.send_message(chat_id, caption, parse_mode='html')

            # Convert and send each track
            for filename in files:
                if filename.lower().endswith('.flac'):
                    input_path = os.path.join(album_path, filename)
                    output_path = f"{input_path}.{format_choice}"
                    if format_choice == 'flac':
                        subprocess.run(['ffmpeg', '-n', '-i', input_path, output_path])
                    elif format_choice == 'mp3':
                        subprocess.run(['ffmpeg', '-n', '-i', input_path, '-b:a', '320k', output_path])

                    audio = File(output_path, easy=True)
                    artist = audio.get('artist', ['Unknown Artist'])[0]
                    title = audio.get('title', ['Unknown Title'])[0]
                    for field in ['artist', 'title', 'album', 'genre']:
                        if field in audio:
                            audio[field] = [value.replace(";", ", ") for value in audio[field]]
                    audio.save()
                    final_name = f"{artist} - {title}.{format_choice}".replace(";", ", ")
                    final_path = os.path.join(album_path, final_name)
                    os.rename(output_path, final_path)
                    await client.send_file(chat_id, final_path)

            shutil.rmtree(root_path)
            increment_download(chat_id, content_type)

        else:  # track
            download_dir = f'downloads/{components[-1]}'
            filename = os.listdir(download_dir)[0]
            filepath = f'{download_dir}/{filename}'
            converted_filepath = f'{download_dir}/{filename}.{format_choice}'

            if format_choice == 'flac':
                subprocess.run(['ffmpeg', '-n', '-i', filepath, converted_filepath])
            elif format_choice == 'mp3':
                subprocess.run(['ffmpeg', '-n', '-i', filepath, '-b:a', '320k', converted_filepath])

            audio = File(converted_filepath, easy=True)
            artist = audio.get('artist', ['Unknown Artist'])[0]
            title = audio.get('title', ['Unknown Title'])[0]
            for field in ['artist', 'title', 'album', 'genre']:
                if field in audio:
                    audio[field] = [value.replace(";", ", ") for value in audio[field]]
            audio.save()
            new_filename = f"{artist} - {title}.{format_choice}".replace(";", ", ")
            new_filepath = f'{download_dir}/{new_filename}'
            os.rename(converted_filepath, new_filepath)
            await client.send_file(chat_id, new_filepath)
            shutil.rmtree(download_dir)
            increment_download(chat_id, content_type)

    except Exception as e:
        await client.send_message(chat_id, f"❌ An error occurred during conversion: {e}")

def load_users():
    if not os.path.exists(USERS_FILE):
        return {}
    with open(USERS_FILE, 'r') as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f)

def reset_if_needed(user):
    today_str = datetime.utcnow().strftime('%Y-%m-%d')
    if user.get("last_reset") != today_str:
        user["album_today"] = 0
        user["track_today"] = 0
        user["last_reset"] = today_str

def is_user_allowed(user_id, content_type):
    if user_id in ADMIN_IDS:
        return True
    users = load_users()
    user = users.get(str(user_id), {})
    reset_if_needed(user)
    if user.get('expiry'):
        if datetime.strptime(user['expiry'], '%Y-%m-%d') > datetime.utcnow():
            return True
    if content_type == 'album' and user.get("album_today", 0) >= 2:
        return False
    if content_type == 'track' and user.get("track_today", 0) >= 2:
        return False
    return True

def increment_download(user_id, content_type):
    if user_id in ADMIN_IDS:
        return
    users = load_users()
    uid = str(user_id)
    if uid not in users:
        users[uid] = {}
    user = users[uid]
    reset_if_needed(user)
    if content_type == 'album':
        user["album_today"] = user.get("album_today", 0) + 1
    elif content_type == 'track':
        user["track_today"] = user.get("track_today", 0) + 1
    save_users(users)

def whitelist_user(user_id):
    users = load_users()
    users[str(user_id)] = {
        "expiry": (datetime.utcnow() + timedelta(days=30)).strftime('%Y-%m-%d'),
        "album_today": 0,
        "track_today": 0,
        "last_reset": datetime.utcnow().strftime('%Y-%m-%d')
    }
    save_users(users)

def remove_user(user_id):
    users = load_users()
    if str(user_id) in users:
        users.pop(str(user_id))
        save_users(users)
        return True
    return False

client = TelegramClient(session_name, api_id, api_hash)

# ======================
# Download Queue System
# ======================

async def enqueue_download(chat_id, url, content_type, format_choice):
    """
    Add a download request to the queue.
    """
    global download_queue
    download_queue.append((chat_id, url, content_type, format_choice))
    async with queue_lock:
        await process_queue()


async def process_queue():
    """
    Process the download queue one by one.
    """
    global is_downloading
    while download_queue:
        if is_downloading:
            break  # Already processing, wait
        is_downloading = True
        chat_id, url, content_type, format_choice = download_queue.popleft()
        try:
            await client.send_message(chat_id, "🎶 Your download has started, please wait...")
            await run_orpheus(url, content_type, format_choice, chat_id)
        except Exception as e:
            await client.send_message(chat_id, f"❌ Error during download: {e}")
        finally:
            is_downloading = False


async def run_orpheus(url, content_type, format_choice, chat_id):
    """
    Run Orpheus asynchronously and handle post-processing.
    """
    process = await asyncio.create_subprocess_exec(
        'python', 'orpheus.py', url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise Exception(stderr.decode())

    # Notify user
    await client.send_message(chat_id, f"✅ Download finished! Now converting to {format_choice.upper()}...")

    # 🔑 Convert and send files to user
    await convert_and_send(chat_id, url, content_type, format_choice)


async def convert_and_send(chat_id, input_text, content_type, format_choice):
    """
    Convert downloaded files and send to the user.
    """
    url = urlparse(input_text)
    components = url.path.split('/')
    release_id = components[-1]

    try:
        if content_type == "album":
            root_path = f'downloads/{release_id}'
            flac_files = [f for f in os.listdir(root_path) if f.lower().endswith('.flac')]
            subdirs = [d for d in os.listdir(root_path) if os.path.isdir(os.path.join(root_path, d))]
            album_path = root_path if flac_files else os.path.join(root_path, subdirs[0])
            files = os.listdir(album_path)

            # Collect metadata
            all_artists = set()
            catalog_number = 'N/A'
            for f in files:
                if f.lower().endswith('.flac'):
                    audio = File(os.path.join(album_path, f), easy=True)
                    if audio:
                        for key in ('artist', 'performer', 'albumartist'):
                            if key in audio:
                                all_artists.update(audio[key])
                        if 'catalog' in audio:
                            catalog_number = audio['catalog'][0]

            sample_file = next((f for f in files if f.lower().endswith('.flac')), None)
            sample_path = os.path.join(album_path, sample_file) if sample_file else None
            metadata = File(sample_path, easy=True) if sample_path else {}

            album = metadata.get('album', ['Unknown Album'])[0]
            genre = metadata.get('genre', ['Unknown Genre'])[0]
            bpm = metadata.get('bpm', ['--'])[0]
            label = metadata.get('label', ['--'])[0]
            date = metadata.get('date', ['--'])[0]
            artists_str = ", ".join(sorted(all_artists))

            caption = (
                f"<b>\U0001F3B6 Album:</b> {album}\n"
                f"<b>\U0001F464 Artists:</b> {artists_str}\n"
                f"<b>\U0001F3A7 Genre:</b> {genre}\n"
                f"<b>\U0001F4BF Label:</b> {label}\n"
                f"<b>\U0001F4C5 Release Date:</b> {date}\n"
                f"<b>\U0001F9E9 BPM:</b> {bpm}\n"
            )

            cover_file = next((os.path.join(album_path, f) for f in files if f.lower().startswith('cover') and f.lower().endswith(('.jpg', '.jpeg', '.png'))), None)
            if cover_file:
                await client.send_file(chat_id, cover_file, caption=caption, parse_mode='html')
            else:
                await client.send_message(chat_id, caption, parse_mode='html')

            # Convert and send each track
            for filename in files:
                if filename.lower().endswith('.flac'):
                    input_path = os.path.join(album_path, filename)
                    output_path = f"{input_path}.{format_choice}"
                    if format_choice == 'flac':
                        subprocess.run(['ffmpeg', '-n', '-i', input_path, output_path])
                    elif format_choice == 'mp3':
                        subprocess.run(['ffmpeg', '-n', '-i', input_path, '-b:a', '320k', output_path])

                    audio = File(output_path, easy=True)
                    artist = audio.get('artist', ['Unknown Artist'])[0]
                    title = audio.get('title', ['Unknown Title'])[0]
                    for field in ['artist', 'title', 'album', 'genre']:
                        if field in audio:
                            audio[field] = [value.replace(";", ", ") for value in audio[field]]
                    audio.save()
                    final_name = f"{artist} - {title}.{format_choice}".replace(";", ", ")
                    final_path = os.path.join(album_path, final_name)
                    os.rename(output_path, final_path)
                    await client.send_file(chat_id, final_path)

            shutil.rmtree(root_path)
            increment_download(chat_id, content_type)

        else:  # track
            download_dir = f'downloads/{components[-1]}'
            filename = os.listdir(download_dir)[0]
            filepath = f'{download_dir}/{filename}'
            converted_filepath = f'{download_dir}/{filename}.{format_choice}'

            if format_choice == 'flac':
                subprocess.run(['ffmpeg', '-n', '-i', filepath, converted_filepath])
            elif format_choice == 'mp3':
                subprocess.run(['ffmpeg', '-n', '-i', filepath, '-b:a', '320k', converted_filepath])

            audio = File(converted_filepath, easy=True)
            artist = audio.get('artist', ['Unknown Artist'])[0]
            title = audio.get('title', ['Unknown Title'])[0]
            for field in ['artist', 'title', 'album', 'genre']:
                if field in audio:
                    audio[field] = [value.replace(";", ", ") for value in audio[field]]
            audio.save()
            new_filename = f"{artist} - {title}.{format_choice}".replace(";", ", ")
            new_filepath = f'{download_dir}/{new_filename}'
            os.rename(converted_filepath, new_filepath)
            await client.send_file(chat_id, new_filepath)
            shutil.rmtree(download_dir)
            increment_download(chat_id, content_type)

    except Exception as e:
        await client.send_message(chat_id, f"❌ An error occurred during conversion: {e}")

# === START HANDLER WITH IMAGE & BUTTONS ===
@client.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    banner_path = 'banner.gif'  # Your banner image/gif in working dir
    caption = (
        "🎧 Hey DJ! 🎶\n\n"
        "Welcome to Beatport Downloader Bot – your assistant for downloading full Beatport tracks & albums.\n\n"
        "❓ What I Can Do:\n"
        "🎵 Download original-quality Beatport releases\n"
        "📁 Send you organized, tagged audio files\n\n"
        "📋 Commands:\n"
        "➤ /download beatport url – Start download\n"
        "➤ /myaccount – Check daily usage\n\n"
        "🚀 Paste a Beatport link now and let’s get those bangers!"
    )
    buttons = [
        [Button.url("💟 Support", PAYMENT_URL), Button.url("📨 Contact", "https://t.me/zackantdev")]
    ]
    if os.path.exists(banner_path):
        await client.send_file(event.chat_id, banner_path, caption=caption, buttons=buttons)
    else:
        await event.reply(caption, buttons=buttons)

@client.on(events.NewMessage(pattern='/add'))
async def add_user_handler(event):
    if event.sender_id not in ADMIN_IDS:
        await event.reply("❌ You're not authorized to perform this action.")
        return
    try:
        parts = event.message.text.split()
        if len(parts) < 2:
            await event.reply("⚠️ Usage: /add <user_id> [days]\nExample: /add 123456789 15")
            return

        user_id = int(parts[1])
        days = int(parts[2]) if len(parts) > 2 else 30

        expiry_date = (datetime.utcnow() + timedelta(days=days)).strftime('%Y-%m-%d')

        users = load_users()
        users[str(user_id)] = {
            "expiry": expiry_date,
            "album_today": 0,
            "track_today": 0,
            "last_reset": datetime.utcnow().strftime('%Y-%m-%d')
        }
        save_users(users)

        await event.reply(f"✅ User {user_id} has been granted access for {days} days (until {expiry_date}).")
    except Exception as e:
        await event.reply(f"⚠️ Failed to add user: {e}")

@client.on(events.NewMessage(pattern='/remove'))
async def remove_user_handler(event):
    if event.sender_id not in ADMIN_IDS:
        await event.reply("❌ You're not authorized to perform this action.")
        return
    try:
        user_id = int(event.message.text.split(maxsplit=1)[1])
        removed = remove_user(user_id)
        if removed:
            await event.reply(f"✅ User {user_id} has been removed and now has daily limits.")
        else:
            await event.reply(f"ℹ️ User {user_id} was not found in the whitelist.")
    except Exception as e:
        await event.reply(f"⚠️ Failed to remove user: {e}")

@client.on(events.NewMessage(pattern='/myaccount'))
async def myaccount_handler(event):
    user_id = str(event.chat_id)
    users = load_users()
    user = users.get(user_id, {})
    reset_if_needed(user)

    # Check for premium
    expiry = user.get("expiry")
    if expiry and datetime.strptime(expiry, '%Y-%m-%d') > datetime.utcnow():
        await event.reply(
            f"<b>🎧 Account Status: Premium</b>\n\n"
            f"✅ Unlimited downloads until <b>{expiry}</b>\n"
            f"💟 Thank you for supporting the project!",
            parse_mode='html'
        )
        return

    # Default (free user) response
    album_left = 2 - user.get("album_today", 0)
    track_left = 2 - user.get("track_today", 0)
    msg = (f"<b>🎧 Daily Download Usage</b>\n\n"
           f"📀 Albums: {album_left}/2 remaining\n"
           f"🎵 Tracks: {track_left}/2 remaining\n"
           f"🔁 Resets every 24 hours\n")
    await event.reply(msg, parse_mode='html')

@client.on(events.NewMessage(pattern='/download'))
async def download_handler(event):
    try:
        user_id = event.chat_id
        input_text = event.message.text.split(maxsplit=1)[1].strip()
        is_track = re.match(beatport_track_pattern, input_text)
        is_album = re.match(beatport_album_pattern, input_text)

        if is_track or is_album:
            content_type = 'album' if is_album else 'track'

            if not is_user_allowed(user_id, content_type):
                await event.reply(
                    "🚫 You've reached today's free download limit (2 albums / 2 tracks).\n"
                    "To unlock unlimited downloads for 30 days, please support with a $5 payment and send the proof to @zackantdev",
                    buttons=[Button.url("💳 Pay $5", PAYMENT_URL)]
                )
                return

            state[event.chat_id] = {"url": input_text, "type": content_type}
            await event.reply("Please choose the format:", buttons=[
                [Button.inline("MP3 (320 kbps)", b"mp3"), Button.inline("FLAC (16 Bit)", b"flac")]
            ])
        else:
            await event.reply('Invalid link.\nPlease send a valid Beatport track or album URL.')
    except Exception as e:
        await event.reply(f"An error occurred: {e}")

@client.on(events.CallbackQuery)
async def callback_query_handler(event):
    try:
        format_choice = event.data.decode('utf-8')
        url_info = state.get(event.chat_id)
        if not url_info:
            await event.edit("No URL found. Please start again using /download.")
            return

        input_text = url_info["url"]
        content_type = url_info["type"]

        await event.edit(f"✅ Your download has been queued. You will receive it shortly!")
        # Enqueue download
        await enqueue_download(event.chat_id, input_text, content_type, format_choice)
        del state[event.chat_id]  # clear temporary state

    except Exception as e:
        await event.reply(f"An error occurred: {e}")

@client.on(events.NewMessage(pattern='/broadcast'))
async def broadcast_handler(event):
    if event.sender_id not in ADMIN_IDS:
        await event.reply("❌ You're not authorized to use this command.")
        return

    try:
        args = event.message.text.split(maxsplit=1)
        if len(args) < 2:
            await event.reply("⚠️ Please provide a message to broadcast. Usage:\n<b>/broadcast Your message here</b>", parse_mode='html')
            return
        broadcast_message = args[1]

        users = load_users()
        count = 0
        failed = 0
        for uid, data in users.items():
            if int(uid) in ADMIN_IDS:
                continue
            if 'expiry' in data:
                try:
                    expiry = datetime.strptime(data['expiry'], '%Y-%m-%d')
                    if expiry > datetime.utcnow():
                        continue  # Skip whitelisted users
                except:
                    pass
            try:
                await client.send_message(int(uid), f"📢 <b>Announcement</b>\n\n{broadcast_message}", parse_mode='html')
                count += 1
            except Exception as e:
                print(f"Failed to send to {uid}: {e}")
                failed += 1

        await event.reply(f"✅ Broadcast sent to <b>{count}</b> users.\n❌ Failed to send to <b>{failed}</b> users.", parse_mode='html')
    except Exception as e:
        await event.reply(f"⚠️ An error occurred while broadcasting: {e}")


@client.on(events.NewMessage(pattern='/adminlist'))
async def admin_list_handler(event):
    if event.sender_id not in ADMIN_IDS:
        await event.reply("❌ You're not authorized to use this command.")
        return
    lines = ["<b>👑 Admin Users:</b>\n"]
    for admin_id in ADMIN_IDS:
        try:
            user = await client.get_entity(admin_id)
            username = f"@{user.username}" if user.username else "No username"
            lines.append(f"• <code>{admin_id}</code> – {username}")
        except Exception:
            lines.append(f"• <code>{admin_id}</code> – [Could not fetch username]")
    await event.reply("\n".join(lines), parse_mode='html')


@client.on(events.NewMessage(pattern='/whitelist'))
async def whitelist_handler(event):
    if event.sender_id not in ADMIN_IDS:
        await event.reply("❌ You're not authorized to use this command.")
        return
    users = load_users()
    now = datetime.utcnow()
    lines = ["<b>📜 Whitelisted Users (Premium):</b>\n"]
    count = 0
    for uid, data in users.items():
        if 'expiry' in data:
            try:
                expiry = datetime.strptime(data['expiry'], '%Y-%m-%d')
                if expiry > now:
                    try:
                        user = await client.get_entity(int(uid))
                        username = f"@{user.username}" if user.username else "No username"
                    except Exception:
                        username = "[Could not fetch username]"
                    lines.append(f"• <code>{uid}</code> – {username} (expires: {data['expiry']})")
                    count += 1
            except:
                continue

    if count == 0:
        lines.append("No active whitelisted users found.")
    await event.reply("\n".join(lines), parse_mode='html')

# === NEW COMMAND: /totalusers ===
@client.on(events.NewMessage(pattern='/totalusers'))
async def total_users_handler(event):
    if event.sender_id not in ADMIN_IDS:
        await event.reply("❌ You're not authorized to use this command.")
        return
    users = load_users()
    total = len(users)
    await event.reply(f"👥 Total registered users: <b>{total}</b>", parse_mode='html')

@client.on(events.NewMessage(pattern='/alert'))
async def alert_expiry_handler(event):
    if event.sender_id not in ADMIN_IDS:
        await event.reply("❌ You're not authorized to use this command.")
        return

    users = load_users()
    now = datetime.utcnow().date()
    notified = 0
    failed = 0

    for uid, data in users.items():
        expiry_str = data.get('expiry')
        if not expiry_str:
            continue

        try:
            expiry = datetime.strptime(expiry_str, '%Y-%m-%d').date()
            days_left = (expiry - now).days

            if days_left in [1, 2, 3]:
                if days_left == 3:
                    message = (
                        f"⏳ <b>Heads up!</b>\n\n"
                        f"Your premium access will expire in <b>3 days</b> on <b>{expiry_str}</b>.\n"
                        f"Renew early to enjoy uninterrupted downloads!"
                    )
                elif days_left == 2:
                    message = (
                        f"⏳ <b>Reminder:</b>\n\n"
                        f"Your premium access will expire in <b>2 days</b> on <b>{expiry_str}</b>.\n"
                        f"Don’t forget to renew and keep the music flowing!"
                    )
                elif days_left == 1:
                    message = (
                        f"⚠️ <b>Final Reminder:</b>\n\n"
                        f"Your premium access expires <b>TOMORROW</b> (<b>{expiry_str}</b>).\n"
                        f"Renew now to avoid losing your unlimited access."
                    )

                try:
                    await client.send_message(
                        int(uid),
                        message,
                        parse_mode='html',
                        buttons=[
                            [Button.url("💳 Donate Here", PAYMENT_URL)],
                            [Button.url("📨 Contact @zackantdev", "https://t.me/zackantdev")]
                        ]
                    )
                    notified += 1
                except Exception as e:
                    print(f"❌ Failed to message {uid}: {e}")
                    failed += 1
        except Exception as e:
            print(f"⚠️ Error parsing expiry for user {uid}: {e}")
            continue

    await event.reply(
        f"✅ Expiry alerts sent to <b>{notified}</b> users.\n❌ Failed for <b>{failed}</b> users.",
        parse_mode='html'
    )
    
async def main():
    async with client:
        print("Client is running...")
        await client.run_until_disconnected()

if __name__ == '__main__':
    client.loop.run_until_complete(main())
