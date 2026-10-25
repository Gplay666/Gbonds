"""Загрузка музыкальной библиотеки с диска.

Метаданные читаются через ffprobe: исполнитель, альбом, жанр, год, номер
трека и длительность. Поддерживаются как обычные аудио-расширения, так и
файлы без расширения (формат определяется по содержимому).
"""

import json
import os
import re
import subprocess

from track import Track


AUDIO_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".ogg",
    ".oga",
    ".opus",
    ".wav",
    ".m4a",
    ".aac",
    ".wma",
    ".aiff",
    ".aif",
    ".ape",
    ".alac",
}


def _clean(value, default=""):
    """Убирает BOM, лишние пробелы и служебные символы из тега."""
    if value is None:
        return default
    value = str(value).replace("\ufeff", "").strip()
    return value or default


def _tags(probe_data):
    if not probe_data:
        return {}
    raw = probe_data.get("format", {}).get("tags", {}) or {}
    return {
        str(key).lower(): _clean(value)
        for key, value in raw.items()
    }


def _tag(tags, *names, default=""):
    for name in names:
        value = tags.get(name.lower())
        if value:
            return value
    return default


def _filename_parts(filename):
    """Пытается разобрать имя вида 'Artist - Title.mp3'."""
    ext = _get_extension(filename)
    stem = filename[:-len(ext)] if ext else filename
    # Убираем типовые префиксы вида "02. " или "02) ".
    stem = re.sub(r"^\s*\d+[\.\)]\s*", "", stem)
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem.strip()


def _prefer_readable(tag_value, filename_value):
    """Если тег записан латиницей, а в имени файла есть кириллица — берём имя файла."""
    if not tag_value:
        return filename_value
    if not filename_value:
        return tag_value

    def has_cyrillic(value):
        return any("\u0400" <= char <= "\u04ff" for char in value)

    if has_cyrillic(filename_value) and not has_cyrillic(tag_value):
        return filename_value
    return tag_value


def _parse_year(value):
    match = re.search(r"(\d{4})", value or "")
    return int(match.group(1)) if match else None


def _parse_track_number(value):
    match = re.search(r"(\d+)", value or "")
    return int(match.group(1)) if match else None


def probe_file(path):
    """Возвращает разобранный JSON ffprobe или None при ошибке."""
    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        path,
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        return None

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def has_audio_stream(probe_data):
    if not probe_data:
        return False
    return any(
        stream.get("codec_type") == "audio"
        for stream in probe_data.get("streams", [])
    )


def extract_metadata(probe_data, filename):
    """Извлекает метаданные трека.

    Возвращает словарь с ключами title, artist, album, genre, year,
    track_number, duration. При отсутствии тегов использует имя файла.
    """
    if not probe_data or not has_audio_stream(probe_data):
        return None

    fmt = probe_data.get("format", {}) or {}
    tags = _tags(probe_data)

    file_artist, file_title = _filename_parts(filename)

    title = _tag(tags, "title")
    artist = _tag(tags, "artist", "album_artist", "albumartist")
    album = _tag(tags, "album")
    genre = _tag(tags, "genre")
    date = _tag(tags, "date", "year")
    track_number = _tag(tags, "track", "tracknumber")

    title = _prefer_readable(title, file_title)
    artist = _prefer_readable(artist, file_artist)

    if not title:
        title = file_title or os.path.splitext(filename)[0]
    if not artist:
        artist = "Unknown"

    try:
        duration = int(round(float(fmt.get("duration", 0) or 0)))
    except (TypeError, ValueError):
        duration = 0

    return {
        "title": title,
        "artist": artist,
        "album": album or "Unknown",
        "genre": genre,
        "year": _parse_year(date),
        "track_number": _parse_track_number(track_number),
        "duration": duration,
    }


def _fingerprint(meta):
    return (
        meta["artist"].strip().casefold(),
        meta["title"].strip().casefold(),
        meta["album"].strip().casefold(),
        int(meta["duration"]),
    )


def _max_numeric_id(system):
    maximum = 0
    for track in system.table.values():
        try:
            maximum = max(maximum, int(track.id))
        except (TypeError, ValueError):
            continue
    return maximum


def _next_free_id(system, start):
    candidate = start
    while system.table.find(str(candidate)) is not None:
        candidate += 1
    return candidate


def _get_extension(path):
    """Расширение файла; для имён вида '02. Название' возвращает ''."""
    name = os.path.basename(path)
    if "." not in name:
        return ""
    ext = os.path.splitext(name)[1].lower()
    # os.path.splitext считает расширением всё после последней точки,
    # даже если это часть названия с пробелами.
    if any(ch.isspace() for ch in ext):
        return ""
    return ext


def _is_audio_candidate(path):
    ext = _get_extension(path)
    if ext in AUDIO_EXTENSIONS:
        return True
    # Файлы без расширения проверяем через ffprobe: в тестовой папке
    # встречается mp3 без суффикса.
    return ext == ""


def scan_music_directory(directory: str, system, progress=None):
    """Рекурсивно загружает аудиофайлы из папки.

    Возвращает статистику: loaded, skipped, errors.
    """
    stats = {
        "loaded": 0,
        "skipped": 0,
        "added_ids": [],
        "errors": [],
    }

    if not os.path.isdir(directory):
        stats["errors"].append(f"Папка не найдена: {directory}")
        return stats

    existing_paths = set()
    existing_fingerprints = set()
    tracks_by_path = {}
    for track in system.table.values():
        if track.filepath:
            normalized = os.path.realpath(track.filepath)
            existing_paths.add(normalized)
            tracks_by_path[normalized] = track
        if track.filepath and track.album and track.album != "Unknown":
            existing_fingerprints.add((
                str(track.artist).strip().casefold(),
                str(track.title).strip().casefold(),
                str(track.album).strip().casefold(),
                int(track.duration or 0),
            ))

    auto_id = _next_free_id(system, _max_numeric_id(system) + 1)
    files = sorted(
        os.path.join(root, name)
        for root, _, names in os.walk(directory)
        for name in names
    )

    for index, path in enumerate(files, start=1):
        if progress:
            progress(index, len(files), path)

        if not _is_audio_candidate(path):
            continue

        real_path = os.path.realpath(path)
        if real_path in existing_paths:
            existing_track = tracks_by_path.get(real_path)
            if existing_track is not None and (
                existing_track.file_mtime_ns is None
                or existing_track.file_size is None
            ):
                try:
                    file_stat = os.stat(path)
                except OSError:
                    pass
                else:
                    existing_track.file_mtime_ns = file_stat.st_mtime_ns
                    existing_track.file_size = file_stat.st_size
            stats["skipped"] += 1
            continue

        probe = probe_file(path)
        if not has_audio_stream(probe):
            # Для файлов с аудио-расширением это ошибка, для остальных — пропуск.
            if _get_extension(path) in AUDIO_EXTENSIONS:
                stats["errors"].append(f"Не удалось прочитать аудио: {path}")
            else:
                stats["skipped"] += 1
            continue

        try:
            meta = extract_metadata(probe, os.path.basename(path))
        except Exception as exc:
            stats["errors"].append(f"{os.path.basename(path)}: {exc}")
            continue

        if meta is None:
            stats["skipped"] += 1
            continue

        fingerprint = _fingerprint(meta)
        if fingerprint in existing_fingerprints:
            stats["skipped"] += 1
            continue

        track_id = str(auto_id)
        auto_id = _next_free_id(system, auto_id + 1)

        try:
            file_stat = os.stat(path)
            file_mtime_ns = file_stat.st_mtime_ns
            file_size = file_stat.st_size
        except OSError:
            file_mtime_ns = None
            file_size = None

        track = Track(
            track_id=track_id,
            title=meta["title"],
            artist=meta["artist"],
            genre=meta["genre"],
            duration=meta["duration"],
            rating=5,
            filepath=os.path.abspath(path),
            album=meta["album"],
            year=meta["year"],
            track_number=meta["track_number"],
            file_mtime_ns=file_mtime_ns,
            file_size=file_size,
        )

        if system.add_track(track):
            existing_paths.add(os.path.realpath(path))
            existing_fingerprints.add(fingerprint)
            stats["loaded"] += 1
            stats["added_ids"].append(str(track.id))
        else:
            stats["errors"].append(
                f"Не удалось добавить трек {meta['artist']} - {meta['title']}"
            )

    return stats
