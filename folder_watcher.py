"""Наблюдение за музыкальными папками.

Модуль не изменяет аудиофайлы: он только читает их размер и mtime.
Снимок папок сравнивается с предыдущим, а обновление библиотеки выполняет
верхний уровень приложения.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable, Mapping, Optional

from audio_loader import AUDIO_EXTENSIONS
from folder_store import normalize_folder, path_is_within


FileSignature = tuple[int, int]


def _extension(path: str) -> str:
    """Расширение в том же виде, в каком его понимает audio_loader."""
    name = os.path.basename(path)
    if "." not in name:
        return ""
    extension = os.path.splitext(name)[1].lower()
    if any(char.isspace() for char in extension):
        return ""
    return extension


def is_audio_candidate(path: str) -> bool:
    extension = _extension(path)
    return extension in AUDIO_EXTENSIONS or extension == ""


@dataclass(frozen=True)
class FolderSnapshot:
    roots: dict[str, bool]
    files: dict[str, FileSignature]
    errors: tuple[str, ...] = ()
    root_errors: frozenset[str] = frozenset()


@dataclass(frozen=True)
class FolderChange:
    added: frozenset[str] = frozenset()
    removed: frozenset[str] = frozenset()
    modified: frozenset[str] = frozenset()

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.removed or self.modified)


def collect_snapshot(folders: Iterable[str]) -> FolderSnapshot:
    """Собирает mtime/size всех потенциальных аудиофайлов в папках."""
    roots: dict[str, bool] = {}
    files: dict[str, FileSignature] = {}
    errors: list[str] = []
    root_errors: set[str] = set()

    for raw_folder in folders:
        root = normalize_folder(raw_folder)
        if root in roots:
            continue
        exists = os.path.isdir(root)
        roots[root] = exists
        if not exists:
            continue

        def on_error(exc, root=root):
            errors.append(f"{root}: {exc}")
            root_errors.add(root)

        for directory, _, names in os.walk(root, onerror=on_error):
            for name in names:
                path = os.path.join(directory, name)
                if not is_audio_candidate(path):
                    continue
                try:
                    stat = os.stat(path)
                except OSError as exc:
                    # Файл мог исчезнуть между обходом и stat(). Это не
                    # ошибка доступа ко всему корню, поэтому не защищаем
                    # остальные файлы папки от обработки.
                    errors.append(f"{path}: {exc}")
                    continue
                files[os.path.realpath(path)] = (
                    stat.st_mtime_ns,
                    stat.st_size,
                )
    return FolderSnapshot(
        roots=roots,
        files=files,
        errors=tuple(errors),
        root_errors=frozenset(root_errors),
    )


def library_snapshot(
    tracks: Iterable,
    folders: Iterable[str],
) -> dict[str, Optional[FileSignature]]:
    """Возвращает сохранённые в библиотеке подписи файлов."""
    roots = [normalize_folder(folder) for folder in folders]
    result: dict[str, Optional[FileSignature]] = {}
    for track in tracks:
        filepath = getattr(track, "filepath", None)
        if not filepath:
            continue
        if not any(path_is_within(filepath, root) for root in roots):
            continue
        mtime = getattr(track, "file_mtime_ns", None)
        size = getattr(track, "file_size", None)
        signature = (
            (int(mtime), int(size))
            if mtime is not None and size is not None
            else None
        )
        result[os.path.realpath(filepath)] = signature
    return result


def diff_snapshots(
    previous: Mapping[str, Optional[FileSignature]],
    current: Mapping[str, FileSignature],
    roots: Mapping[str, bool],
) -> FolderChange:
    """Сравнивает два снимка.

    Файлы из отсутствующих корней не считаются удалёнными: внешний диск
    мог быть просто отключён, и библиотека не должна из-за этого очищаться.
    """
    existing_roots = [root for root, exists in roots.items() if exists]

    added = frozenset(path for path in current if path not in previous)
    removed = frozenset(
        path
        for path in previous
        if path not in current
        and any(path_is_within(path, root) for root in existing_roots)
    )
    modified = frozenset(
        path
        for path in previous.keys() & current.keys()
        if previous[path] is not None and previous[path] != current[path]
    )
    return FolderChange(added=added, removed=removed, modified=modified)
