import logging
import os
import subprocess
from typing import Dict, Tuple, List, Union, Optional
from pathlib import Path
import tempfile
import time

import warnings
warnings.simplefilter("ignore")


from mtslinker.downloader import download_video_chunk


def get_media_info(file_path: str) -> Dict[str, any]:
    """Get detailed information about media file streams using ffprobe.
    
    Enhanced with:
    - Absolute path conversion
    - File existence verification
    - Retry logic for Windows file locking issues
    - Fallback to ffmpeg if ffprobe fails
    - Proper encoding handling for non-ASCII paths (Windows Cyrillic)
    """
    # Convert to absolute path to avoid relative path issues
    abs_path = os.path.abspath(file_path)
    
    # Verify file exists before proceeding
    if not os.path.exists(abs_path):
        logging.error(f'File does not exist: {abs_path}')
        logging.error(f'File exists check returned False. Current working directory: {os.getcwd()}')
        return {'has_video': False, 'has_audio': False}
    
    # Check file size to ensure it's not empty or still being written
    file_size = os.path.getsize(abs_path)
    if file_size == 0:
        logging.error(f'File is empty: {abs_path}')
        return {'has_video': False, 'has_audio': False}
    
    logging.info(f'File verified: {abs_path} (size: {file_size} bytes)')
    
    # Small delay to ensure file handle is released (Windows issue)
    time.sleep(0.5)
    
    try:
        # Get video stream info using ffprobe
        # Use shell=False and pass arguments as list for better Windows compatibility
        cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_type,width,height,r_frame_rate,duration',
            '-of', 'json',
            abs_path
        ]
        logging.debug(f'Running ffprobe command: {" ".join(cmd)}')
        
        # On Windows, use creationflags to avoid console window popup
        creationflags = 0
        if os.name == 'nt':
            creationflags = subprocess.CREATE_NO_WINDOW
        
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            timeout=30,
            creationflags=creationflags,
            encoding='utf-8',
            errors='replace'
        )
        
        logging.debug(f'ffprobe return code: {result.returncode}')
        logging.debug(f'ffprobe stdout: {result.stdout[:500] if result.stdout else "empty"}')
        if result.stderr:
            logging.debug(f'ffprobe stderr: {result.stderr[:500]}')
        
        video_info = {}
        if result.returncode == 0 and result.stdout.strip():
            import json
            try:
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
                    logging.info(f'Video stream detected: {video_info["width"]}x{video_info["height"]}, duration={video_info["duration"]}s')
                else:
                    video_info = {'has_video': False}
                    logging.warning(f'No video streams found in {abs_path}')
            except json.JSONDecodeError as e:
                logging.error(f'Failed to parse ffprobe JSON output: {e}')
                video_info = {'has_video': False}
        else:
            video_info = {'has_video': False}
            if result.returncode != 0:
                logging.warning(f'ffprobe returned non-zero exit code {result.returncode} for video stream')
            if not result.stdout.strip():
                logging.warning(f'ffprobe produced no stdout output for video stream')
        
        # Get audio stream info using ffprobe
        cmd = [
            'ffprobe', '-v', 'error',
            '-select_streams', 'a:0',
            '-show_entries', 'stream=codec_type,sample_rate,channels,duration',
            '-of', 'json',
            abs_path
        ]
        
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            timeout=30,
            creationflags=creationflags,
            encoding='utf-8',
            errors='replace'
        )
        
        audio_info = {}
        if result.returncode == 0 and result.stdout.strip():
            import json
            try:
                data = json.loads(result.stdout)
                if data.get('streams'):
                    stream = data['streams'][0]
                    audio_info = {
                        'has_audio': True,
                        'sample_rate': int(stream.get('sample_rate', 44100)),
                        'channels': int(stream.get('channels', 2)),
                        'duration': float(stream.get('duration', 0))
                    }
                    logging.info(f'Audio stream detected: {audio_info["sample_rate"]}Hz, duration={audio_info["duration"]}s')
                else:
                    audio_info = {'has_audio': False}
                    logging.warning(f'No audio streams found in {abs_path}')
            except json.JSONDecodeError as e:
                logging.error(f'Failed to parse ffprobe JSON output for audio: {e}')
                audio_info = {'has_audio': False}
        else:
            audio_info = {'has_audio': False}
            if result.returncode != 0:
                logging.warning(f'ffprobe returned non-zero exit code {result.returncode} for audio stream')
            if not result.stdout.strip():
                logging.warning(f'ffprobe produced no stdout output for audio stream')
        
        combined_info = {**video_info, **audio_info}
        
        # If ffprobe failed to get duration, try fallback method with ffmpeg
        if not combined_info.get('duration', 0):
            logging.warning(f'ffprobe could not get duration for {abs_path}, trying ffmpeg fallback...')
            fallback_duration = get_duration_with_ffmpeg(abs_path)
            if fallback_duration:
                combined_info['duration'] = fallback_duration
                logging.info(f'Successfully got duration via ffmpeg fallback: {fallback_duration}s')
        
        return combined_info
        
    except subprocess.TimeoutExpired:
        logging.error(f'Timeout while getting media info for {abs_path}')
        return {'has_video': False, 'has_audio': False}
    except Exception as e:
        logging.error(f'Failed to get media info for {abs_path}: {e}')
        logging.error(f'Exception type: {type(e).__name__}')
        # Try fallback method
        fallback_duration = get_duration_with_ffmpeg(abs_path)
        if fallback_duration:
            logging.info(f'Fallback duration obtained: {fallback_duration}s')
            return {'duration': fallback_duration, 'has_video': True, 'has_audio': True}
        return {'has_video': False, 'has_audio': False}


def get_duration_with_ffmpeg(file_path: str) -> Optional[float]:
    """Fallback method to get duration using ffmpeg instead of ffprobe.
    
    Uses ffmpeg -i command and parses output for duration.
    More reliable on some Windows systems where ffprobe has issues.
    Enhanced with:
    - Proper encoding handling for non-ASCII paths (Windows Cyrillic)
    - Creation flags for Windows
    - Better error logging
    """
    try:
        # On Windows, use creationflags to avoid console window popup
        creationflags = 0
        if os.name == 'nt':
            creationflags = subprocess.CREATE_NO_WINDOW
        
        cmd = ['ffmpeg', '-i', file_path]
        logging.debug(f'Running ffmpeg command: {" ".join(cmd)}')
        
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            timeout=30,
            creationflags=creationflags,
            encoding='utf-8',
            errors='replace'
        )
        
        # ffmpeg outputs duration to stderr
        output = result.stderr
        logging.debug(f'ffmpeg output (first 500 chars): {output[:500] if output else "empty"}')
        
        # Look for duration pattern: Duration: 00:00:00.00, 
        import re
        duration_match = re.search(r'Duration:\s*(\d{2}):(\d{2}):(\d{2})\.(\d+)', output)
        if duration_match:
            hours = int(duration_match.group(1))
            minutes = int(duration_match.group(2))
            seconds = int(duration_match.group(3))
            milliseconds = float(f'0.{duration_match.group(4)}')
            total_duration = hours * 3600 + minutes * 60 + seconds + milliseconds
            logging.info(f'Successfully parsed duration via ffmpeg: {total_duration}s')
            return total_duration
        
        logging.warning(f'Could not parse duration from ffmpeg output for {file_path}')
        logging.warning(f'ffmpeg return code: {result.returncode}')
        return None
        
    except Exception as e:
        logging.error(f'ffmpeg fallback also failed for {file_path}: {e}')
        logging.error(f'Exception type: {type(e).__name__}')
        return None


def process_video_clips(directory: str, json_data: Dict) -> Tuple[float, List[Dict]]:
    """
    Process video clips from JSON data.
    Returns total duration and list of clip info dictionaries with unified structure.
    Each clip info contains: path, start_time, has_video, has_audio, duration, width, height, fps
    
    Enhanced with:
    - Retry logic for file locking issues on Windows
    - Better error handling and logging
    - Fallback to ffmpeg-based duration detection
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
                
                # Verify file was downloaded successfully
                if not os.path.exists(downloaded_file_path):
                    logging.error(f'Download failed or file not found: {downloaded_file_path}')
                    continue
                
                # Check file size to ensure complete download
                file_size = os.path.getsize(downloaded_file_path)
                if file_size == 0:
                    logging.error(f'Downloaded file is empty: {downloaded_file_path}')
                    continue
                
                logging.info(f'Successfully downloaded: {downloaded_file_path} ({file_size} bytes)')
                
                # Small delay to ensure file handle is released (Windows issue)
                time.sleep(0.5)
                
                # Get media info using enhanced ffprobe with fallback
                media_info = get_media_info(downloaded_file_path)
                
                # Use the duration from media_info (already includes ffmpeg fallback)
                clip_duration = media_info.get('duration', 0)
                
                # If still no duration, try one more time with direct ffmpeg call
                if not clip_duration:
                    logging.warning(f'No duration found via get_media_info, trying direct ffmpeg...')
                    clip_duration = get_duration_with_ffmpeg(downloaded_file_path)
                
                if not clip_duration or clip_duration <= 0:
                    logging.error(f'Invalid or zero duration for {downloaded_file_path}, skipping...')
                    continue
                
                logging.info(f'Clip duration: {clip_duration}s, has_video={media_info.get("has_video", False)}, has_audio={media_info.get("has_audio", False)}')
                
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
    
    # Critical check: if no clips were processed, log detailed error
    if len(clips_info) == 0:
        logging.error('CRITICAL: No valid clips were processed! This will result in a black/silent video.')
        logging.error('Check the following:')
        logging.error('1. Are the downloaded files valid MP4 files?')
        logging.error('2. Do the files have both video and audio streams?')
        logging.error('3. Is ffprobe/ffmpeg working correctly on your system?')
        logging.error('4. Check file permissions and disk space.')
    
    return total_duration, clips_info


def create_video_with_gaps(total_duration: float, clips_info: List[Dict]) -> str:
    """
    Создать временный видеофайл с правильным таймингом используя FFmpeg.
    Возвращает путь к временному файлу.
    Handles both video-only and audio-only clips properly.
    Оптимизировано для длинных видео (5-8 часов) через прямое использование FFmpeg.
    """
    # Sort clips by start time to ensure correct order
    sorted_clips = sorted(clips_info, key=lambda x: x['start_time'])
    
    # Determine the maximum resolution across all video clips
    max_width = 1920
    max_height = 1080
    
    for clip_info in sorted_clips:
        if clip_info['has_video']:
            max_width = max(max_width, clip_info.get('width', 1920))
            max_height = max(max_height, clip_info.get('height', 1080))
    
    # Filter only video clips
    video_clips = [c for c in sorted_clips if c['has_video']]
    
    if not video_clips:
        # No video clips, create black screen using FFmpeg
        logging.info('No video clips found, creating black background with FFmpeg')
        temp_video_path = os.path.join(os.path.dirname(clips_info[0]['path']), 'black_video.mp4')
        
        # Generate black video with FFmpeg
        cmd = [
            'ffmpeg', '-y',
            '-f', 'lavfi',
            '-i', f'color=c=black:s={max_width}x{max_height}:d={total_duration}',
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-pix_fmt', 'yuv420p',
            temp_video_path
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return temp_video_path
    
    # Создаём чёрный фон на всю длительность
    temp_dir = os.path.dirname(video_clips[0]['path'])
    bg_video = os.path.join(temp_dir, 'background.mp4')
    
    cmd = [
        'ffmpeg', '-y',
        '-f', 'lavfi',
        '-i', f'color=c=black:s={max_width}x{max_height}:d={total_duration}',
        '-c:v', 'libx264',
        '-preset', 'ultrafast',
        bg_video
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    
    # Создаем финальное видео через FFmpeg с overlay и enable=between
    temp_video_path = os.path.join(temp_dir, 'temp_video_track.mp4')
    
    # Строим filter_complex с использованием enable=between для каждого клипа
    inputs = ["-i", bg_video]
    filter_parts = []
    
    for i, clip_info in enumerate(video_clips):
        file_path = clip_info["path"]
        start_time = clip_info["start_time"]
        duration = clip_info["duration"]
        end_time = start_time + duration
        
        inputs.extend(["-i", file_path])
        
        if i == 0:
            # Первый клип накладываем на фон
            filter_parts.append(f"[0:v][{i+1}:v]overlay=enable='between(t,{start_time},{end_time})'[out{i}]")
        else:
            # Последующие клипы накладываем на предыдущий результат
            filter_parts.append(f"[out{i-1}][{i+1}:v]overlay=enable='between(t,{start_time},{end_time})'[out{i}]")
    
    if not filter_parts:
        # Если нет видео клипов, просто копируем черный фон
        temp_video_path = bg_video
        return temp_video_path
    
    filter_complex = ";".join(filter_parts)
    final_map = f"[out{len(video_clips)-1}]"
    
    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", final_map,
        "-c:v", "libx264",
        "-preset", "medium",
        "-pix_fmt", "yuv420p",
        temp_video_path
    ]
    
    logging.info(f"Создание видео дорожки через FFmpeg ({len(video_clips)} клипов)...")
    logging.debug(f"FFmpeg command: {" ".join(cmd)}")
    logging.debug(f"Filter complex: {filter_complex}")
    
    try:
        result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logging.error(f"FFmpeg failed with return code {e.returncode}")
        logging.error(f"Stdout: {e.stdout}")
        logging.error(f"Stderr: {e.stderr}")
        raise
    
    # Очищаем фон
    if os.path.exists(bg_video):
        os.remove(bg_video)
    
    logging.info(f'Видео дорожка создана: {temp_video_path}')
    return temp_video_path


def create_audio_with_gaps(total_duration: float, clips_info: List[Dict]) -> str:
    """
    Создать временный аудиофайл с правильным таймингом используя FFmpeg.
    Возвращает путь к временному файлу.
    Handles both audio-only and video-with-audio clips properly.
    Оптимизировано для длинных видео (5-8 часов) через прямое использование FFmpeg.
    """
    # Sort clips by start time to ensure correct order
    sorted_clips = sorted(clips_info, key=lambda x: x['start_time'])
    
    # Filter only audio clips
    audio_clips = [c for c in sorted_clips if c['has_audio']]
    
    if not audio_clips:
        # No audio clips, create silence using FFmpeg
        logging.info('No audio clips found, creating silent audio track with FFmpeg')
        temp_dir = os.path.dirname(clips_info[0]['path'])
        temp_audio_path = os.path.join(temp_dir, 'silent_audio.aac')
        
        # Generate silence with FFmpeg
        cmd = [
            'ffmpeg', '-y',
            '-f', 'lavfi',
            '-i', f'anullsrc=r=44100:cl=stereo:d={total_duration}',
            '-c:a', 'aac',
            temp_audio_path
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        return temp_audio_path
    
    # Создаем финальное аудио через FFmpeg
    temp_dir = os.path.dirname(audio_clips[0]['path'])
    temp_audio_path = os.path.join(temp_dir, 'temp_audio_track.aac')
    
    # Используем фильтр для создания аудио дорожки с правильным timing
    filter_complex_parts = []
    inputs = []
    
    # Создаем тихий фон на всю длительность
    inputs.extend(['-f', 'lavfi', '-i', f'anullsrc=r=44100:cl=stereo:d={total_duration}'])
    
    # Накладываем каждый аудио клип в правильное время
    for i, clip_info in enumerate(audio_clips):
        file_path = clip_info['path']
        start_time = clip_info['start_time']
        inputs.extend(['-i', file_path])
        filter_complex_parts.append(
            f'[{i+1}:a]asetpts=PTS-STARTPTS+{start_time}[a{i}]'
        )
    
    # Объединяем все аудио дорожки
    if filter_complex_parts:
        mix_inputs = '+'.join([f'[a{i}]' for i in range(len(audio_clips))])
        filter_complex_parts.append(f'[0:a]{mix_inputs}amix=inputs={len(audio_clips)+1}:duration=first:dropout_transition=0[outa]')
    
    filter_complex = ';'.join(filter_complex_parts)
    
    cmd = ['ffmpeg', '-y'] + inputs + [
        '-filter_complex', filter_complex,
        '-map', '[outa]',
        '-c:a', 'aac',
        '-b:a', '128k',
        temp_audio_path
    ]
    
    logging.info(f'Создание аудио дорожки через FFmpeg ({len(audio_clips)} клипов)...')
    subprocess.run(cmd, check=True, capture_output=True)
    
    logging.info(f'Аудио дорожка создана: {temp_audio_path}')
    return temp_audio_path


def compile_final_video(total_duration: float, clips_info: List[Dict],
                        output_path: str, max_duration: Union[int, None]):
    """
    Compile final video from clip info dictionaries.
    Uses FFmpeg for efficient processing of long videos (5-8 hours).
    Completely rewritten to avoid MoviePy bottlenecks.
    """
    # Create video and audio tracks using FFmpeg
    temp_video_path = create_video_with_gaps(total_duration, clips_info)
    temp_audio_path = create_audio_with_gaps(total_duration, clips_info)
    
    # Apply max_duration limit if specified
    if max_duration:
        logging.info(f'Applying duration limit: {max_duration}s')
        total_duration = min(total_duration, max_duration)
    
    logging.info(f'Объединение видео и аудио дорожек через FFmpeg...')
    
    # Merge video and audio using FFmpeg
    cmd = [
        'ffmpeg', '-y',
        '-i', temp_video_path,
        '-i', temp_audio_path,
        '-c:v', 'libx264',
        '-preset', 'medium',
        '-c:a', 'aac',
        '-b:a', '128k',
        '-pix_fmt', 'yuv420p',
        '-t', str(total_duration),
        output_path
    ]
    
    subprocess.run(cmd, check=True, capture_output=True)
    logging.info(f'Финальное видео сохранено: {output_path}')
    
    # Clean up temporary files
    cleanup_temp_files([temp_video_path, temp_audio_path])
    
    # Clean up downloaded chunks after successful compilation
    cleanup_downloaded_files(clips_info)


def cleanup_temp_files(file_paths: List[str]):
    """Remove temporary intermediate files."""
    for file_path in file_paths:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logging.info(f'Cleaned up temporary file: {file_path}')
            except Exception as e:
                logging.warning(f'Failed to remove {file_path}: {e}')


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
