import re
import os
import aiofiles
import asyncio

from shutil import copyfileobj
from xml.etree import ElementTree

from .tidal_api import tidalapi


async def parse_url(url):
    patterns = [
        (r"/browse/track/(\d+)", "track"),
        (r"/browse/artist/(\d+)", "artist"),
        (r"/browse/album/(\d+)", "album"),
        (r"/browse/playlist/([\w-]+)", "playlist"),
        (r"/track/(\d+)", "track"),
        (r"/artist/(\d+)", "artist"),
        (r"/playlist/([\w-]+)", "playlist"),
        (r"/album/\d+/track/(\d+)", "track"),
        (r"/album/(\d+)", "album"),
    ]
    
    for pattern, type_ in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), type_
    
    return None, None


async def get_stream_session(track_data: dict):
    media_tags = track_data['mediaMetadata']['tags']
    format = None

    if 'SONY_360RA' in media_tags and tidalapi.spatial == 'Sony 360RA':
        format = '360ra'
    elif 'DOLBY_ATMOS' in media_tags and tidalapi.spatial == 'ATMOS AC3 JOC':
        format = 'ac3'
    elif 'DOLBY_ATMOS' in media_tags and tidalapi.spatial == 'ATMOS AC4':
        format = 'ac4'
    elif 'HIRES_LOSSLESS' in media_tags and tidalapi.quality == 'HI_RES':
        format = 'flac_hires'

    session = {
        'flac_hires': tidalapi.mobile_hires,
        '360ra': tidalapi.mobile_hires if tidalapi.mobile_hires else tidalapi.mobile_atmos,
        'ac4': tidalapi.mobile_atmos,
        'ac3': tidalapi.tv_session,
        None: tidalapi.tv_session,
    }[format]

    if not format and 'DOLBY_ATMOS' in media_tags:
        if tidalapi.mobile_hires:
            session = tidalapi.mobile_hires

    quality = tidalapi.quality if format != 'flac_hires' else 'HI_RES_LOSSLESS'
    
    return session, quality
    


def parse_mpd(xml: bytes) -> list:
    xml = xml.decode('UTF-8')
    xml = re.sub(r'xmlns="[^"]+"', '', xml, count=1)
    root = ElementTree.fromstring(xml)

    tracks = []

    for period in root.findall('Period'):
        for adaptation_set in period.findall('AdaptationSet'):
            for rep in adaptation_set.findall('Representation'):
                if adaptation_set.get('contentType') != 'audio':
                    raise ValueError('Only supports audio MPDs!')

                codec = rep.get('codecs').upper()
                if codec.startswith('MP4A'):
                    codec = 'AAC'

                seg_template = rep.find('SegmentTemplate')
                track_urls = [seg_template.get('initialization')]
                start_number = int(seg_template.get('startNumber') or 1)

                seg_timeline = seg_template.find('SegmentTimeline')
                if seg_timeline is not None:
                    seg_time_list = []
                    cur_time = 0

                    for s in seg_timeline.findall('S'):
                        if s.get('t'):
                            cur_time = int(s.get('t'))

                        for i in range((int(s.get('r') or 0) + 1)):
                            seg_time_list.append(cur_time)
                            cur_time += int(s.get('d'))

                    seg_num_list = list(range(start_number, len(seg_time_list) + start_number))
                    track_urls += [seg_template.get('media').replace('$Number$', str(n)) for n in seg_num_list]

                tracks.append(track_urls)

    return tracks, codec


async def convert_to_mp3(flac_path: str, mp3_path: str = None, bitrate: str = "320k"):
    """
    Convert a FLAC file to MP3 using FFmpeg asynchronously.
    """
    if not os.path.exists(flac_path):
        raise FileNotFoundError(f"FLAC file not found: {flac_path}")

    if mp3_path is None:
        mp3_path = os.path.splitext(flac_path)[0] + ".mp3"

    cmd = f'ffmpeg -y -i "{flac_path}" -vn -c:a libmp3lame -b:a {bitrate} "{mp3_path}" -loglevel error'
    
    process = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        raise RuntimeError(f"FFmpeg failed:\n{stderr.decode()}")

    return mp3_path


async def merge_tracks(temp_tracks: list, output_path: str, convert_mp3: bool = True):
    """
    Merge multiple track segments into a single FLAC file.
    Optionally converts to MP3 after merging and deletes the FLAC.
    """
    async with aiofiles.open(output_path, 'wb') as dest_file:
        for temp_location in temp_tracks:
            async with aiofiles.open(temp_location, 'rb') as segment_file:
                while True:
                    chunk = await segment_file.read(1024 * 64)
                    if not chunk:
                        break
                    await dest_file.write(chunk)
    
    # Delete temp segment files asynchronously
    delete_tasks = [asyncio.to_thread(os.remove, temp_location) for temp_location in temp_tracks]
    await asyncio.gather(*delete_tasks)

    if convert_mp3:
        mp3_path = await convert_to_mp3(output_path)
        # Delete original FLAC after conversion
        await asyncio.to_thread(os.remove, output_path)
        return mp3_path

    return output_path


async def get_quality(stream_data: dict):
    quality_dict = {
        'LOW':'LOW',
        'HIGH':'HIGH',
        'LOSSLESS':'LOSSLESS',
        'HI_RES':'MAX',
        'HI_RES_LOSSLESS':'MAX'
    }

    if stream_data['audioMode'] == 'DOLBY_ATMOS':
        return 'Dolby ATMOS'
    return quality_dict[stream_data['audioQuality']]


async def sort_album_from_artist(album_data: dict):
    albums = []

    for album in album_data:
        if album['audioModes'] == ['DOLBY_ATMOS'] and tidalapi.spatial in ['ATMOS AC3 JOC', 'ATMOS AC4']:
            albums.append(album)
        elif album['audioModes'] == ['STEREO'] and tidalapi.spatial == 'OFF':
            albums.append(album)

    unique_albums = {}

    for album in albums:
        unique_key = (album['title'], album['version'])
        if unique_key not in unique_albums:
            unique_albums[unique_key] = album
        else:
            existing_metadata = unique_albums[unique_key].get('mediaMetadata', {})
            new_metadata = album.get('mediaMetadata', {})
            if len(new_metadata) > len(existing_metadata):  
                unique_albums[unique_key] = album

    filtered_tracks = list(unique_albums.values())
    return filtered_tracks
