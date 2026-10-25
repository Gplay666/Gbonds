"""Пользовательские плейлисты: имена и упорядоченные списки ID треков."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

from app_paths import playlists_path


PLAYLIST_FORMAT = "tui-music-playlists"
PLAYLIST_VERSION = 1


def default_playlists_path() -> Path:
    return playlists_path()


class PlaylistStore:
    """Хранилище плейлистов с атомарным сохранением в JSON."""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path).expanduser() if path else default_playlists_path()
        self.playlists: dict[str, list[str]] = {}
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
            self.error = f"Не удалось прочитать плейлисты: {exc}"
            return
        if not isinstance(snapshot, dict) or snapshot.get("format") != PLAYLIST_FORMAT:
            self.error = "Файл плейлистов имеет неизвестный формат."
            return
        if snapshot.get("version") != PLAYLIST_VERSION:
            self.error = "Версия файла плейлистов не поддерживается."
            return
        raw = snapshot.get("playlists")
        if not isinstance(raw, dict):
            self.error = "В файле плейлистов нет списка плейлистов."
            return
        for name, ids in raw.items():
            if not isinstance(name, str) or not name.strip():
                continue
            if not isinstance(ids, list):
                continue
            self.playlists[name.strip()] = [str(item) for item in ids]

    def save(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        snapshot = {
            "format": PLAYLIST_FORMAT,
            "version": PLAYLIST_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "playlists": self.playlists,
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
        return list(self.playlists.keys())

    def get(self, name: str) -> list[str]:
        return list(self.playlists.get(name, []))

    def count(self, name: str) -> int:
        return len(self.playlists.get(name, []))

    def create(self, name: str) -> str:
        clean = name.strip()
        if not clean:
            raise ValueError("Имя плейлиста не может быть пустым.")
        if len(clean) > 80:
            raise ValueError("Имя плейлиста слишком длинное.")
        if clean in self.playlists:
            raise ValueError("Плейлист с таким именем уже существует.")
        self.playlists[clean] = []
        self.save()
        return clean

    def delete(self, name: str) -> bool:
        if name not in self.playlists:
            return False
        del self.playlists[name]
        self.save()
        return True

    def rename(self, old: str, new: str) -> str:
        clean = new.strip()
        if old not in self.playlists:
            raise ValueError("Плейлист не найден.")
        if not clean:
            raise ValueError("Имя плейлиста не может быть пустым.")
        if clean in self.playlists and clean != old:
            raise ValueError("Плейлист с таким именем уже существует.")
        self.playlists[clean] = self.playlists.pop(old)
        self.save()
        return clean

    def add(self, name: str, track_ids: Iterable[str]) -> int:
        if name not in self.playlists:
            raise ValueError("Плейлист не найден.")
        ids = [str(track_id) for track_id in track_ids]
        self.playlists[name].extend(ids)
        if ids:
            self.save()
        return len(ids)

    def remove_at(self, name: str, index: int) -> bool:
        if name not in self.playlists:
            return False
        ids = self.playlists[name]
        if not 0 <= index < len(ids):
            return False
        ids.pop(index)
        self.save()
        return True

    def clear(self, name: str) -> bool:
        if name not in self.playlists:
            return False
        self.playlists[name].clear()
        self.save()
        return True

    def remove_track_everywhere(self, track_id: str) -> int:
        """Убирает трек из всех плейлистов (например, при удалении папки)."""
        target = str(track_id)
        removed = 0
        changed = False
        for name, ids in self.playlists.items():
            kept = [item for item in ids if item != target]
            count = len(ids) - len(kept)
            if count:
                self.playlists[name] = kept
                removed += count
                changed = True
        if changed:
            self.save()
        return removed

    def total_items(self) -> int:
        return sum(len(ids) for ids in self.playlists.values())
