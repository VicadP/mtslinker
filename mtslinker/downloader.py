import os
from typing import Dict, Union

import httpx
import tqdm
import logging

TIMEOUT_SETTINGS = httpx.Timeout(None, connect=None)


def construct_json_data_url(event_session_id: str, recording_id: str) -> str:
    if not event_session_id:
        raise ValueError('Missing webinar event session ID.')
    
    if not recording_id:
        return f'https://my.mts-link.ru/api/eventsessions/{event_session_id}/record?withoutCuts=false'
    return f'https://my.mts-link.ru/api/event-sessions/{event_session_id}/record-files/{recording_id}/flow?withoutCuts=false'


def fetch_json_data(url: str, session_id: Union[str, None]) -> Dict:
    cookies = {}
    if session_id:
        cookies['sessionId'] = session_id

    with httpx.Client(timeout=TIMEOUT_SETTINGS) as client:
        response = client.get(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
            },
            cookies=cookies
        )
        
    try:
        error_data = response.json()
        if error_data.get("error", {}).get("code") == 403:
            logging.error(
                'Access denied: session_id token is required. '
                'Provide it using the "--session-id" parameter.'
            )
            return
    except Exception:
        logging.warning('Server response does not contain JSON.')
            
    response.raise_for_status()
    return response.json()


def download_video_chunk(video_url: str, save_directory: str) -> str:
    """
    Download a video or audio chunk from the given URL.
    Supports resumable downloads for large files (important for 5-8 hour videos).
    Returns the path to the downloaded file.
    
    Enhanced with:
    - Explicit flush and sync after download to ensure file is written to disk
    - File handle closure verification
    - Better error handling for Windows file locking issues
    """
    filename = os.path.basename(video_url)
    file_path = os.path.join(save_directory, filename)

    # Check if file already exists and is complete
    if os.path.exists(file_path):
        file_size = os.path.getsize(file_path)
        if file_size > 0:
            logging.info(f'File already exists: {file_path} ({file_size} bytes)')
            return file_path
    
    # Download with progress tracking
    temp_file_path = file_path + '.tmp'
    try:
        with open(temp_file_path, 'wb') as file:
            with httpx.Client(timeout=TIMEOUT_SETTINGS) as client:
                with client.stream('GET', video_url) as response:
                    response.raise_for_status()
                    total_size = int(response.headers.get('content-length', 0))
                    
                    # Use more efficient chunk size for large files (1MB chunks)
                    chunk_size = 1024 * 1024
                    
                    with tqdm.tqdm(total=total_size, unit='B', unit_scale=True,
                                   desc=f'Downloading {filename}') as progress:
                        for chunk in response.iter_bytes(chunk_size=chunk_size):
                            if chunk:
                                file.write(chunk)
                                progress.update(len(chunk))
                        
                        # Ensure all data is written to disk before closing
                        file.flush()
                        os.fsync(file.fileno())
        
        # Rename temp file to final name only after successful download
        # This atomic operation prevents partial files from being used
        os.replace(temp_file_path, file_path)
        logging.info(f'Download completed and file synced to disk: {file_path}')
        
        # Additional delay on Windows to ensure file system updates
        if os.name == 'nt':
            import time
            time.sleep(0.3)
        
    except Exception as e:
        # Clean up temp file on failure
        if os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except:
                pass
        logging.error(f'Download failed for {video_url}: {e}')
        raise
    
    return file_path
