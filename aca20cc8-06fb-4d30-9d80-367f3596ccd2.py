import re
import os
import aiofiles
import asyncio

from shutil import copyfileobj
from xml.etree import ElementTree

from .tidal_api import tidalapi


async def parse_url(url):
    """
    Parse url type and ID from Tidal URL
    Args:
        url (str): Tidal URL.
    Returns:
        id: int
        type: str
    """
    patterns = [
        (r"/browse/track/(\d+)", "track"),  # Track from browse
        (r"/browse/artist/(\d+)", "artist"),  # Artist from browse
        (r"/browse/album/(\d+)", "album"),  # Album from browse
        (r"/browse/playlist/([\w-]+)", "playlist"),  # Playlist with numeric or UUID
        (r"/track/(\d+)", "track"),  # Track from listen.tidal.com
        (r"/artist/(\d+)", "artist"),  # Artist from listen.tidal.com
        (r"/playlist/([\w-]+)", "playlist"),  # Playlist with numeric or UUID
        (r"/album/\d+/track/(\d+)", "track"),  # Extract only track ID from album_and_track
        (r"/album/(\d+)", "album"),
    ]
    
    for pattern, type_ in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1), type_
    
    return None, None


async def get_stream_session(track_data: dict):
    """
    Session needed for the quality chosen
    Args:
        track_data: raw data for the track
    Returns:
        session: TidalSession
        quality: LOW | HIGH | LOSSLESS | HI_RES | HI_RES_LOSSLESS
    """
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
                content_type = adaptation_set.get('contentType')
                if content_type != 'audio':
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


async def merge_tracks(temp_tracks: list, output_path: str):
    async with aiofiles.open(output_path, 'wb') as dest_file:
        for temp_location in temp_tracks:
            async with aiofiles.open(temp_location, 'rb') as segment_file:
                while True:
                    chunk = await segment_file.read(1024 * 64)
                    if not chunk:
                        break
                    await dest_file.write(chunk)
    
    delete_tasks = [asyncio.to_thread(os.remove, temp_location) for temp_location in temp_tracks]
    await asyncio.gather(*delete_tasks)


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


async def ffmpeg_convert(input_file):
    """
    Convert downloaded audio to FLAC, then to 320 kbps MP3.
    Deletes both the original input file and FLAC after conversion.
    """
    flac_path = f'{input_file}.flac'
    mp3_path = f'{input_file}.mp3'

    # Step 1: Convert to FLAC
    cmd_flac = f'ffmpeg -i "{input_file}" -c:a copy -loglevel error -y "{flac_path}"'
    flac_task = await asyncio.create_subprocess_shell(cmd_flac)
    await flac_task.wait()

    # Step 2: Convert FLAC → MP3 320 kbps
    cmd_mp3 = f'ffmpeg -i "{flac_path}" -vn -ar 44100 -ac 2 -b:a 320k -loglevel error -y "{mp3_path}"'
    mp3_task = await asyncio.create_subprocess_shell(cmd_mp3)
    await mp3_task.wait()

    # Step 3: Delete original input and FLAC
    for file in [input_file, flac_path]:
        try:
            os.remove(file)
        except Exception as e:
            print(f"Warning: Could not delete {file}: {e}")

    return mp3_path
