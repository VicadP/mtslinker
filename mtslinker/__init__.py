"""
mtslinker - Tool for downloading and processing MTS Link webinar recordings.

This package provides functionality to:
- Download video and audio chunks from MTS Link webinars
- Properly synchronize audio and video tracks
- Compile final video files with correct A/V sync
- Handle long-duration videos (5-8 hours) efficiently
"""

from mtslinker.utils import initialize_logger
from mtslinker.webinar import download_webinar, fetch_webinar_data

initialize_logger()

__version__ = '1.0.0'
__all__ = ['download_webinar', 'fetch_webinar_data', 'initialize_logger']