import logging
import os
import subprocess
from typing import Dict, Tuple, List, Union, Optional
from pathlib import Path
import tempfile

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
    inputs = ['-i', bg_video]
    filter_parts = ['[0:v]']
    
    for i, clip_info in enumerate(video_clips):
        file_path = clip_info['path']
        start_time = clip_info['start_time']
        duration = clip_info['duration']
        end_time = start_time + duration
        
        inputs.extend(['-i', file_path])
        
        if i == 0:
            # Первый клип накладываем на фон
            filter_parts.append(f'[{i+1}:v]overlay=enable=\'between(t,{start_time},{end_time})\'[out{i}]')
        else:
            # Последующие клипы накладываем на предыдущий результат
            filter_parts.append(f'[{i-1}out{i-1}][{i+1}:v]overlay=enable=\'between(t,{start_time},{end_time})\'[out{i}]')
    
    filter_complex = ';'.join(filter_parts[:-1]) + ';' + filter_parts[-1].split('[')[-1]
    final_map = filter_parts[-1].split('[')[-1].rstrip(']')
    
    cmd = ['ffmpeg', '-y'] + inputs + [
        '-filter_complex', filter_complex,
        '-map', f'[{final_map}]',
        '-c:v', 'libx264',
        '-preset', 'medium',
        '-pix_fmt', 'yuv420p',
        temp_video_path
    ]
    
    logging.info(f'Создание видео дорожки через FFmpeg ({len(video_clips)} клипов)...')
    subprocess.run(cmd, check=True, capture_output=True)
    
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
