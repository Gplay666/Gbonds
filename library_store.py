"""Сохранение библиотеки и графа связей на диск.

Кэш содержит не только рёбра графа, но и сами треки с результатами
аудио-анализа. Поэтому при следующем запуске плееру не нужно заново
декодировать все файлы и строить рекомендации с нуля.

Формат — обычный JSON. Файл пишется атомарно: сначала во временный файл,
затем заменяет старый, чтобы при сбое не потерять предыдущее сохранение.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from app_paths import analysis_log_path, library_path
from track import Track


CACHE_FORMAT = "tui-music-library"
CACHE_VERSION = 1


def default_library_path():
    """Путь к сохранению по XDG Data Directory.

    Можно переопределить переменной окружения TUI_MUSIC_LIBRARY.
    """
    return library_path()


def default_analysis_log_path():
    return analysis_log_path()


def _json_default(value):
    """Страховка для numpy-скаляров и других нестандартных типов."""
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    if isinstance(value, (set, tuple)):
        return list(value)
    return str(value)


def _track_to_dict(track):
    data = {
        "id": str(track.id),
        "title": track.title,
        "artist": track.artist,
        "genre": track.genre,
        "metadata_genre": getattr(track, "metadata_genre", track.genre),
        "duration": track.duration,
        "rating": track.rating,
        "filepath": track.filepath,
        "album": track.album,
        "year": track.year,
        "track_number": track.track_number,
        "file_mtime_ns": getattr(track, "file_mtime_ns", None),
        "file_size": getattr(track, "file_size", None),
    }
    if track.analysis:
        data["analysis"] = track.analysis
    return data


def save_library(system, path=None):
    """Сохраняет текущую библиотеку и граф. Возвращает статистику."""
    target = Path(path).expanduser() if path else default_library_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    tracks = list(system.table.values())
    edges = [
        {
            "from": str(edge["from"]),
            "to": str(edge["to"]),
            "weight": float(edge["weight"]),
        }
        for edge in system.graph.edges
    ]

    snapshot = {
        "format": CACHE_FORMAT,
        "version": CACHE_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tracks": [_track_to_dict(track) for track in tracks],
        "graph": {
            "directed": bool(system.graph.directed),
            "edges": edges,
        },
    }

    fd, temp_name = tempfile.mkstemp(
        prefix=target.name + ".",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(
                snapshot,
                file,
                ensure_ascii=False,
                indent=1,
                default=_json_default,
            )
            file.write("\n")
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise

    return {
        "path": str(target),
        "tracks": len(tracks),
        "edges": len(edges),
    }


def _restore_track(data):
    track = Track(
        track_id=str(data["id"]),
        title=str(data.get("title") or ""),
        artist=str(data.get("artist") or "Unknown"),
        genre=str(data.get("genre") or ""),
        duration=int(data.get("duration") or 0),
        rating=int(data.get("rating") or 5),
        filepath=data.get("filepath"),
        album=data.get("album"),
        year=data.get("year"),
        track_number=data.get("track_number"),
        file_mtime_ns=data.get("file_mtime_ns"),
        file_size=data.get("file_size"),
    )
    track.metadata_genre = str(
        data.get("metadata_genre") or data.get("genre") or ""
    )

    analysis = data.get("analysis")
    if isinstance(analysis, dict):
        # apply_analysis не требует Essentia: он только раскладывает уже
        # сохранённые признаки по полям Track и нормализует жанр.
        from audio_analysis import apply_analysis

        apply_analysis(track, analysis)

    return track


def load_library(system, path=None):
    """Загружает сохранённую библиотеку и граф.

    Возвращает словарь со статистикой. Если файла нет, found=False.
    Если файл повреждён, текущая библиотека не очищается.
    """
    target = Path(path).expanduser() if path else default_library_path()
    result = {
        "found": target.exists(),
        "path": str(target),
        "loaded": 0,
        "edges": 0,
        "skipped": 0,
        "error": None,
    }
    if not result["found"]:
        return result

    try:
        with open(target, "r", encoding="utf-8") as file:
            snapshot = json.load(file)
    except Exception as exc:
        result["error"] = f"Не удалось прочитать сохранение: {exc}"
        return result

    if not isinstance(snapshot, dict) or snapshot.get("format") != CACHE_FORMAT:
        result["error"] = "Файл сохранения имеет неизвестный формат."
        return result

    version = snapshot.get("version")
    if version != CACHE_VERSION:
        result["error"] = (
            f"Версия сохранения {version!r} не поддерживается "
            f"(нужна {CACHE_VERSION})."
        )
        return result

    raw_tracks = snapshot.get("tracks")
    if not isinstance(raw_tracks, list):
        result["error"] = "В сохранении нет списка треков."
        return result

    tracks = []
    for data in raw_tracks:
        if not isinstance(data, dict) or "id" not in data:
            result["skipped"] += 1
            continue
        try:
            tracks.append(_restore_track(data))
        except Exception:
            result["skipped"] += 1

    # Сначала успешно разобрали файл, и только теперь заменяем библиотеку.
    system.clear()
    known_ids = set()
    for track in tracks:
        if system.add_track(track):
            known_ids.add(str(track.id))
            result["loaded"] += 1
        else:
            result["skipped"] += 1

    graph_data = snapshot.get("graph")
    if not isinstance(graph_data, dict):
        graph_data = {}
    raw_edges = graph_data.get("edges") or []
    if isinstance(raw_edges, list):
        for edge in raw_edges:
            if not isinstance(edge, dict):
                result["skipped"] += 1
                continue
            a = str(edge.get("from", ""))
            b = str(edge.get("to", ""))
            if a not in known_ids or b not in known_ids or a == b:
                result["skipped"] += 1
                continue
            try:
                weight = float(edge.get("weight", 1.0))
            except (TypeError, ValueError):
                weight = 1.0
            system.connect_tracks(a, b, weight)
            result["edges"] += 1

    return result
