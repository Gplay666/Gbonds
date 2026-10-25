"""Список музыкальных папок, общий для всей библиотеки.

Сами аудиофайлы остаются на месте. В каталоге данных приложения хранится
только список корневых папок, которые пользователь добавил в библиотеку.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from app_paths import folders_path


FOLDER_FORMAT = "tui-music-folders"
FOLDER_VERSION = 1


def default_folders_path() -> Path:
    return folders_path()


def normalize_folder(path: str | os.PathLike[str]) -> str:
    """Приводит путь папки к абсолютному каноническому виду."""
    return os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))


def path_is_within(path: str | os.PathLike[str], folder: str | os.PathLike[str]) -> bool:
    """Проверяет, находится ли файл внутри папки."""
    if not path or not folder:
        return False
    try:
        candidate = os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))
        root = os.path.realpath(os.path.abspath(os.path.expanduser(str(folder))))
        return os.path.commonpath([candidate, root]) == root
    except (OSError, ValueError):
        return False


def tracks_in_folder(tracks: Iterable, folder: str) -> list:
    """Возвращает треки, чьи файлы лежат внутри указанной папки."""
    root = normalize_folder(folder)
    return [
        track
        for track in tracks
        if getattr(track, "filepath", None)
        and path_is_within(track.filepath, root)
    ]


def display_folder(path: str) -> str:
    """Сокращает домашний каталог до ``~`` для интерфейса."""
    text = str(path)
    home = str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home + os.sep):
        return "~" + text[len(home):]
    return text


class FolderStore:
    """Хранилище списка музыкальных папок с атомарным JSON-сохранением."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path).expanduser() if path else default_folders_path()
        self.folders: list[str] = []
        self.watch_enabled: bool = True
        self.error: Optional[str] = None
        self.load()

    def load(self) -> None:
        self.error = None
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as file:
                snapshot = json.load(file)
        except Exception as exc:
            self.error = f"Не удалось прочитать список папок: {exc}"
            return
        if not isinstance(snapshot, dict) or snapshot.get("format") != FOLDER_FORMAT:
            self.error = "Файл списка папок имеет неизвестный формат."
            return
        if snapshot.get("version") != FOLDER_VERSION:
            self.error = "Версия файла списка папок не поддерживается."
            return
        raw = snapshot.get("folders")
        if not isinstance(raw, list):
            self.error = "В файле списка папок нет массива folders."
            return

        watch = snapshot.get("watch", True)
        self.watch_enabled = watch if isinstance(watch, bool) else True

        seen = set()
        for item in raw:
            if not isinstance(item, str) or not item.strip():
                continue
            normalized = normalize_folder(item)
            if normalized and normalized not in seen:
                self.folders.append(normalized)
                seen.add(normalized)

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "format": FOLDER_FORMAT,
            "version": FOLDER_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "watch": self.watch_enabled,
            "folders": self.folders,
        }
        fd, temp_name = tempfile.mkstemp(
            prefix=self.path.name + ".",
            suffix=".tmp",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(snapshot, file, ensure_ascii=False, indent=1)
                file.write("\n")
            os.replace(temp_name, self.path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise
        self.error = None
        return self.path

    def names(self) -> list[str]:
        return list(self.folders)

    def __iter__(self):
        return iter(self.folders)

    def __len__(self) -> int:
        return len(self.folders)

    def __contains__(self, path: object) -> bool:
        try:
            return normalize_folder(str(path)) in self.folders
        except (OSError, ValueError):
            return False

    def add(self, path: str | os.PathLike[str]) -> str:
        normalized = normalize_folder(path)
        if not os.path.isdir(normalized):
            raise ValueError(f"Папка не найдена: {path}")
        if normalized in self.folders:
            raise ValueError("Эта папка уже добавлена.")
        for existing in self.folders:
            if path_is_within(normalized, existing):
                raise ValueError(
                    "Эта папка уже входит в добавленную: "
                    f"{display_folder(existing)}"
                )
            if path_is_within(existing, normalized):
                raise ValueError(
                    "Добавленная папка уже содержит: "
                    f"{display_folder(existing)}"
                )
        self.folders.append(normalized)
        self.save()
        return normalized

    def remove(self, path: str) -> bool:
        normalized = normalize_folder(path)
        if normalized not in self.folders:
            return False
        self.folders.remove(normalized)
        self.save()
        return True

    def set_watch_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.watch_enabled == enabled:
            return
        self.watch_enabled = enabled
        self.save()
