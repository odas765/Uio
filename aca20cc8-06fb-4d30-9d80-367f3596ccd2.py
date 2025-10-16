import os
import asyncio
import shutil
import subprocess

from ..settings import bot_set
from .message import send_message, edit_message
from .utils import *

#
#  TASK HANDLER
#

async def track_upload(metadata, user, disable_link=False):
    if bot_set.upload_mode == 'Local':
        await local_upload(metadata, user)
    elif bot_set.upload_mode == 'Telegram':
        await telegram_upload(metadata, user)
    else:
        rclone_link, index_link = await rclone_upload(user, metadata['filepath'])
        if not disable_link:
            await post_simple_message(user, metadata, rclone_link, index_link)

    try:
        os.remove(metadata['filepath'])
    except FileNotFoundError:
        pass


async def album_upload(metadata, user):
    if bot_set.upload_mode == 'Local':
        await local_upload(metadata, user)
    elif bot_set.upload_mode == 'Telegram':
        if bot_set.album_zip:
            for item in metadata['folderpath']:
                await send_message(
                    user,
                    item,
                    'doc',
                    caption=await create_simple_text(metadata, user)
                )
        else:
            await batch_telegram_upload(metadata, user)
    else:
        rclone_link, index_link = await rclone_upload(user, metadata['folderpath'])
        if metadata['poster_msg']:
            try:
                await edit_art_poster(
                    metadata,
                    user,
                    rclone_link,
                    index_link,
                    await format_string(lang.s.ALBUM_TEMPLATE, metadata, user)
                )
            except MessageNotModified:
                pass
        else:
            await post_simple_message(user, metadata, rclone_link, index_link)

    await cleanup(None, metadata)


async def artist_upload(metadata, user):
    if bot_set.upload_mode == 'Local':
        await local_upload(metadata, user)
    elif bot_set.upload_mode == 'Telegram':
        if bot_set.artist_zip:
            for item in metadata['folderpath']:
                await send_message(
                    user,
                    item,
                    'doc',
                    caption=await create_simple_text(metadata, user)
                )
        else:
            pass  # artist telegram uploads handled by album function
    else:
        rclone_link, index_link = await rclone_upload(user, metadata['folderpath'])
        if metadata['poster_msg']:
            try:
                await edit_art_poster(
                    metadata,
                    user,
                    rclone_link,
                    index_link,
                    await format_string(lang.s.ARTIST_TEMPLATE, metadata, user)
                )
            except MessageNotModified:
                pass
        else:
            await post_simple_message(user, metadata, rclone_link, index_link)

    await cleanup(None, metadata)


async def playlist_upload(metadata, user):
    if bot_set.upload_mode == 'Local':
        await local_upload(metadata, user)
    elif bot_set.upload_mode == 'Telegram':
        if bot_set.playlist_zip:
            for item in metadata['folderpath']:
                await send_message(
                    user,
                    item,
                    'doc',
                    caption=await create_simple_text(metadata, user)
                )
        else:
            await batch_telegram_upload(metadata, user)
    else:
        if bot_set.playlist_sort and not bot_set.playlist_zip:
            if bot_set.disable_sort_link:
                await rclone_upload(user, f"{Config.DOWNLOAD_BASE_DIR}/{user['r_id']}/")
            else:
                for track in metadata['tracks']:
                    try:
                        rclone_link, index_link = await rclone_upload(user, track['filepath'])
                        if not bot_set.disable_sort_link:
                            await post_simple_message(user, track, rclone_link, index_link)
                    except ValueError:
                        pass
        else:
            rclone_link, index_link = await rclone_upload(user, metadata['folderpath'])
            if metadata['poster_msg']:
                try:
                    await edit_art_poster(
                        metadata,
                        user,
                        rclone_link,
                        index_link,
                        await format_string(lang.s.PLAYLIST_TEMPLATE, metadata, user)
                    )
                except MessageNotModified:
                    pass
            else:
                await post_simple_message(user, metadata, rclone_link, index_link)

#
#  CORE
#

async def rclone_upload(user, realpath):
    """
    Args:
        user: user details
        realpath: full path to (not used for uploading)
    Returns:
        rclone_link, index_link
    """
    path = f"{Config.DOWNLOAD_BASE_DIR}/{user['r_id']}/"
    cmd = f'rclone copy --config ./rclone.conf "{path}" "{Config.RCLONE_DEST}"'
    task = await asyncio.create_subprocess_shell(cmd)
    await task.wait()
    r_link, i_link = await create_link(realpath, Config.DOWNLOAD_BASE_DIR + f"/{user['r_id']}/")
    return r_link, i_link


async def local_upload(metadata, user):
    """
    Copies directory to local storage and merges contents if the destination exists.
    """
    to_move = f"{Config.DOWNLOAD_BASE_DIR}/{user['r_id']}/{metadata['provider']}"
    destination = os.path.join(Config.LOCAL_STORAGE, os.path.basename(to_move))

    if os.path.exists(destination):
        for item in os.listdir(to_move):
            src_item = os.path.join(to_move, item)
            dest_item = os.path.join(destination, item)
            if os.path.isdir(src_item):
                if not os.path.exists(dest_item):
                    shutil.copytree(src_item, dest_item)
            else:
                shutil.copy2(src_item, dest_item)
    else:
        shutil.copytree(to_move, destination)
    
    shutil.rmtree(to_move)

#
#  Conversion + Telegram Uploads
#

def convert_to_mp3(filepath):
    """
    Converts any audio file to MP3 using ffmpeg.
    Returns the new mp3 file path.
    """
    if filepath.lower().endswith('.mp3'):
        return filepath  # already mp3

    new_path = os.path.splitext(filepath)[0] + ".mp3"
    cmd = [
        "ffmpeg",
        "-y",  # overwrite
        "-i", filepath,
        "-codec:a", "libmp3lame",
        "-qscale:a", "2",
        new_path
    ]
    try:
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return new_path
    except Exception as e:
        print(f"[ERROR] Failed to convert {filepath} to mp3: {e}")
        return filepath


async def telegram_upload(track, user):
    """
    Upload a single track to Telegram (auto converts to mp3 before upload)
    """
    converted_path = convert_to_mp3(track['filepath'])
    await send_message(user, converted_path, 'audio', meta=track)


async def batch_telegram_upload(metadata, user):
    """
    Upload all tracks to Telegram, converting each to mp3 before sending.
    """
    if metadata['type'] in ['album', 'playlist']:
        for track in metadata['tracks']:
            try:
                await telegram_upload(track, user)
            except FileNotFoundError:
                pass
    elif metadata['type'] == 'artist':
        for album in metadata['albums']:
            for track in album['tracks']:
                await telegram_upload(track, user)
