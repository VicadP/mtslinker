import logging
import os
import subprocess
from typing import Dict, Tuple, List, Union, Optional
from pathlib import Path
import tempfile

import numpy as np
from moviepy.audio.AudioClip import AudioArrayClip, CompositeAudioClip
from moviepy.audio.io.AudioFileClip import AudioFileClip
from moviepy.video.VideoClip import ColorClip, VideoClip
from moviepy.video.io.VideoFileClip import VideoFileClip
from moviepy import concatenate_videoclips, CompositeVideoClip

import warnings
warnings.simplefilter("ignore")


from mtslinker.downloader import download_video_chunk


def get_media_info(file_path: str) -> Dict[str, any]:
    """Get detailed information about media file streams using ffprobe."""
    try:
        # Get video stream info
        cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_type,width,height,r_frame_rate,duration',
            '-of', 'json',
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        video_info = {}
        if result.stdout.strip():
            import json
            data = json.loads(result.stdout)
            if data.get('streams'):
                stream = data['streams'][0]
                video_info = {
                    'has_video': True,
                    'width': stream.get('width', 1920),
                    'height': stream.get('height', 1080),
                    'fps': stream.get('r_frame_rate', '30/1'),
                    'duration': float(stream.get('duration', 0))
                }
            else:
                video_info = {'has_video': False}
        else:
            video_info = {'has_video': False}
        
        # Get audio stream info
        cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'a:0',
            '-show_entries', 'stream=codec_type,sample_rate,channels,duration',
            '-of', 'json',
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        audio_info = {}
        if result.stdout.strip():
            import json
            data = json.loads(result.stdout)
            if data.get('streams'):
                stream = data['streams'][0]
                audio_info = {
                    'has_audio': True,
                    'sample_rate': int(stream.get('sample_rate', 44100)),
                    'channels': int(stream.get('channels', 2)),
                    'duration': float(stream.get('duration', 0))
                }
            else:
                audio_info = {'has_audio': False}
        else:
            audio_info = {'has_audio': False}
        
        return {**video_info, **audio_info}
    except Exception as e:
        logging.warning(f'Failed to get media info for {file_path}: {e}')
        return {'has_video': False, 'has_audio': False}


def process_video_clips(directory: str, json_data: Dict) -> Tuple[float, List[Dict]]:
    """
    Process video clips from JSON data.
    Returns total duration and list of clip info dictionaries with unified structure.
    Each clip info contains: path, start_time, has_video, has_audio, duration, width, height, fps
    """
    total_duration = float(json_data.get('duration', 0))
    if not total_duration:
        raise ValueError('Duration not found in JSON data.')

    clips_info = []

    for event in json_data.get('eventLogs', []):
        if isinstance(event, dict):
            data = event.get('data', {})
            if isinstance(data, dict) and 'url' in data:
                url = data['url']
                start_time = event.get('relativeTime', 0) / 1000.0  # Convert ms to seconds

                downloaded_file_path = download_video_chunk(url, directory)
                
                # Get media info using ffprobe
                media_info = get_media_info(downloaded_file_path)
                
                # Use ffprobe duration if available, otherwise fallback to moviepy
                clip_duration = media_info.get('duration', 0)
                
                if not clip_duration:
                    # Fallback to moviepy for duration
                    try:
                        if media_info.get('has_video'):
                            temp_clip = VideoFileClip(downloaded_file_path)
                            clip_duration = temp_clip.duration
                            temp_clip.close()
                        elif media_info.get('has_audio'):
                            temp_clip = AudioFileClip(downloaded_file_path)
                            clip_duration = temp_clip.duration
                            temp_clip.close()
                    except Exception as e:
                        logging.warning(f'Failed to get duration for {downloaded_file_path}: {e}')
                        continue
                
                if not clip_duration or clip_duration <= 0:
                    logging.warning(f'Invalid duration for {downloaded_file_path}')
                    continue
                
                clip_info = {
                    'path': downloaded_file_path,
                    'start_time': start_time,
                    'duration': clip_duration,
                    'has_video': media_info.get('has_video', False),
                    'has_audio': media_info.get('has_audio', False),
                    'width': media_info.get('width', 1920),
                    'height': media_info.get('height', 1080),
                    'fps': media_info.get('fps', '30/1')
                }
                clips_info.append(clip_info)
    
    logging.info(f'Total duration: {total_duration}, processed {len(clips_info)} clips')
    return total_duration, clips_info


def create_video_with_gaps(total_duration: float, clips_info: List[Dict]) -> CompositeVideoClip:
    """
    Create a composite video with proper timing from clip info dictionaries.
    Handles both video-only and audio-only clips properly.
    Uses precise timing to ensure A/V sync for long videos (5-8 hours).
    """
    clips = []
    
    # Sort clips by start time to ensure correct order
    sorted_clips = sorted(clips_info, key=lambda x: x['start_time'])
    
    # Determine the maximum resolution across all video clips
    max_width = 1920
    max_height = 1080
    
    for clip_info in sorted_clips:
        if clip_info['has_video']:
            max_width = max(max_width, clip_info.get('width', 1920))
            max_height = max(max_height, clip_info.get('height', 1080))
    
    for clip_info in sorted_clips:
        start_time = clip_info['start_time']
        file_path = clip_info['path']
        
        if clip_info['has_video']:
            # Load video clip with precise timing
            video_clip = VideoFileClip(file_path)
            video_clip = video_clip.with_start(start_time)
            clips.append(video_clip)
    
    if not clips:
        # No video clips, create black screen
        logging.info('No video clips found, creating black background')
        black_clip = ColorClip(size=(max_width, max_height), color=(0, 0, 0), duration=total_duration)
        return CompositeVideoClip([black_clip.with_start(0)])
    
    # Create composite video with proper size and duration
    final_video = CompositeVideoClip(clips, size=(max_width, max_height))
    final_video = final_video.with_duration(total_duration)
    
    logging.info(f'Final video duration: {final_video.duration}, size: {max_width}x{max_height}')
    return final_video


def create_audio_with_gaps(total_duration: float, clips_info: List[Dict]) -> CompositeAudioClip:
    """
    Create a composite audio track with proper timing from clip info dictionaries.
    Handles both audio-only and video-with-audio clips properly.
    Uses precise timing to ensure A/V sync for long videos (5-8 hours).
    """
    audio_segments = []
    
    # Sort clips by start time to ensure correct order
    sorted_clips = sorted(clips_info, key=lambda x: x['start_time'])
    
    for clip_info in sorted_clips:
        start_time = clip_info['start_time']
        file_path = clip_info['path']
        
        if clip_info['has_audio']:
            # Load audio clip with precise timing
            audio_clip = AudioFileClip(file_path)
            audio_clip = audio_clip.with_start(start_time)
            audio_segments.append(audio_clip)
    
    if not audio_segments:
        # No audio clips, create silence
        logging.info('No audio clips found, creating silent audio track')
        silence = AudioArrayClip(np.zeros((int(total_duration * 44100), 2)), fps=44100)
        return CompositeAudioClip([silence.with_start(0)])
    
    final_audio = CompositeAudioClip(audio_segments)
    final_audio = final_audio.with_duration(total_duration)
    
    logging.info(f'Total audio duration: {final_audio.duration}')
    return final_audio


def compile_final_video(total_duration: float, clips_info: List[Dict],
                        output_path: str, max_duration: Union[int, None]):
    """
    Compile final video from clip info dictionaries.
    Uses FFmpeg for efficient processing of long videos (5-8 hours).
    """
    # Create video and audio tracks
    video_result = create_video_with_gaps(total_duration, clips_info)

    if any(c['has_audio'] for c in clips_info):
        combined_audio = create_audio_with_gaps(total_duration, clips_info)
        video_result = video_result.with_audio(combined_audio)

    if max_duration:
        if video_result.duration > max_duration:
            logging.info(f'Duration limit! Cropping to {max_duration}s')
            video_result = video_result.subclip(0, max_duration)

    # Use more efficient encoding settings for long videos
    video_result.write_videofile(
        output_path,
        codec='libx264',
        audio_codec='aac',
        preset='medium',  # Better compression than ultrafast, still reasonable speed
        threads=os.cpu_count() or 4,
        fps=video_result.fps if hasattr(video_result, 'fps') and video_result.fps else 30,
        audio_fps=44100,
        audio_nbytes=2,
        temp_audiofile=output_path + '.temp.m4a',
        remove_temp=True
    )
    
    # Clean up downloaded chunks after successful compilation
    cleanup_downloaded_files(clips_info)


def cleanup_downloaded_files(clips_info: List[Dict]):
    """Remove downloaded chunk files to save disk space."""
    import os
    for clip_info in clips_info:
        file_path = clip_info.get('path')
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logging.info(f'Cleaned up temporary file: {file_path}')
            except Exception as e:
                logging.warning(f'Failed to remove {file_path}: {e}')
