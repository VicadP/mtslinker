import logging
import os
import re

from mtslinker.downloader import construct_json_data_url, fetch_json_data
from mtslinker.processor import compile_final_video, process_video_clips
from mtslinker.utils import create_directory_if_not_exists


def download_webinar(url: str, output_path: str = None, session_id: str = None, max_duration: int = None):
    """
    Основная точка входа для пользователей.
    
    Скачивает вебинар по указанному URL и сохраняет в заданный путь.
    
    Args:
        url: URL вебинара в формате:
             https://my.mts-link.ru/{org_id}/{room_id}/record-new/{event_sessions}/record-file/{record_id}
             или
             https://my.mts-link.ru/{org_id}/{room_id}/record-new/{event_sessions}
        output_path: Путь для сохранения финального видео (файл или директория).
                     Если не указан, файл сохраняется в текущей директории с именем вебинара.
        session_id: Опциональный токен sessionId для доступа к приватным записям.
        max_duration: Опциональное ограничение длительности видео в секундах.
    
    Returns:
        str: Путь к сохранённому файлу при успехе, None при ошибке.
    
    Example:
        >>> from mtslinker import download_webinar
        >>> result = download_webinar(
        ...     url='https://my.mts-link.ru/12345678/987654321/record-new/123456789/record-file/1234567890',
        ...     output_path='/path/to/save/video.mp4',
        ...     session_id='a1b2c3d4e5f6'
        ... )
        >>> if result:
        ...     print(f'Видео сохранено: {result}')
    """
    # Извлечение ID из URL
    ids = extract_ids_from_url(url)
    if not ids or not ids[0]:
        logging.error('Неверный формат URL. Проверьте ссылку.')
        return None
    
    event_sessions, record_id = ids
    
    logging.info(f'Начало загрузки: event_sessions={event_sessions}, record_id={record_id}')
    
    # Загрузка данных вебинара
    json_data_url = construct_json_data_url(event_session_id=event_sessions, recording_id=record_id)
    json_data = fetch_json_data(url=json_data_url, session_id=session_id)
    
    if not json_data:
        logging.error('Не удалось получить данные вебинара. Проверите session_id или URL.')
        return None
    
    # Определение пути сохранения
    sanitized_name = re.sub(r'[\s\/:*?"<>|]+', '_', json_data['name'])
    
    if output_path is None:
        # Сохранение в текущую директорию с именем вебинара
        directory = create_directory_if_not_exists(sanitized_name)
        output_video_path = os.path.join(directory, f'{sanitized_name}.mp4')
    elif os.path.isdir(output_path):
        # output_path - это директория
        directory = output_path
        output_video_path = os.path.join(directory, f'{sanitized_name}.mp4')
    else:
        # output_path - это полный путь к файлу
        directory = os.path.dirname(output_path)
        if directory and not os.path.exists(directory):
            create_directory_if_not_exists(directory)
        output_video_path = output_path
    
    # Обработка клипов
    total_duration, clips_info = process_video_clips(directory, json_data)
    logging.info(
        f'Загружено и обработано {len(clips_info)} файлов ({total_duration} сек) для склейки.')
    
    # Создание финального видео
    compile_final_video(total_duration, clips_info, output_video_path, max_duration)
    logging.info(f'Финальное видео сохранено в {output_video_path}')
    
    return output_video_path


def extract_ids_from_url(url: str):
    """Извлекает event_sessions и record_id из URL MTS Link."""
    url_pattern = (
        r'^https://my\.mts-link\.ru/(?:[^/]+/)?\d+/\d+/record-new/(\d+)(?:/record-file/(\d+))?$'
    )
    match = re.match(url_pattern, url)

    if match:
        event_sessions = match.group(1)
        record_id = match.group(2) if match.group(2) else None
        return event_sessions, record_id
    
    return None, None


def fetch_webinar_data(event_sessions: str, record_id: str, session_id=None, max_duration=None):
    """
    Устаревшая функция. Используйте download_webinar() вместо этой.
    
    Загружает данные вебинара по ID сессии и записи.
    """
    logging.warning(
        'fetch_webinar_data устарела. Используйте download_webinar(url, output_path, session_id) вместо неё.'
    )
    
    json_data_url = construct_json_data_url(event_session_id=event_sessions, recording_id=record_id)
    json_data = fetch_json_data(url=json_data_url, session_id=session_id)
    
    if not json_data:
        logging.error('Failed to fetch webinar data. Check the session ID or URL.')
        return

    sanitized_name = re.sub(r'[\s\/:*?"<>|]+', '_', json_data['name'])
    directory = create_directory_if_not_exists(sanitized_name)
    output_video_path = os.path.join(directory, f'{sanitized_name}.mp4')

    total_duration, clips_info = process_video_clips(directory, json_data)
    logging.info(
        f'Downloaded and processed {len(clips_info)} files ({total_duration} sec) for merging.')

    compile_final_video(total_duration, clips_info, output_video_path, max_duration)
    logging.info(f'Final video saved to {output_video_path}')
    
    return 1
