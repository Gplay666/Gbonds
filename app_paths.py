"""Пути приложения по XDG Base Directory Specification.

Все пользовательские данные приложения (кэш библиотеки, общий граф
связей, плейлисты и список музыкальных папок) хранятся в одном каталоге:

    $XDG_DATA_HOME/tui-music-recomendator/
    (по умолчанию ~/.local/share/tui-music-recomendator/)

Старые переменные TUI_MUSIC_LIBRARY и TUI_MUSIC_PLAYLISTS сохранены для
совместимости и удобства тестов.
"""

from __future__ import annotations

import os
from pathlib import Path


APP_DIR_NAME = "tui-music-recomendator"


def _xdg_home(variable: str, fallback: Path) -> Path:
    value = os.environ.get(variable)
    if value:
        path = Path(value).expanduser()
        if path.is_absolute():
            return path
    return fallback


def data_dir() -> Path:
    """Каталог данных приложения."""
    override = os.environ.get("TUI_MUSIC_DATA_DIR")
    if override:
        return Path(override).expanduser()
    base = _xdg_home("XDG_DATA_HOME", Path.home() / ".local" / "share")
    return base / APP_DIR_NAME


def library_path() -> Path:
    override = os.environ.get("TUI_MUSIC_LIBRARY")
    if override:
        return Path(override).expanduser()
    return data_dir() / "library.json"


def playlists_path() -> Path:
    override = os.environ.get("TUI_MUSIC_PLAYLISTS")
    if override:
        return Path(override).expanduser()
    return data_dir() / "playlists.json"


def folders_path() -> Path:
    override = os.environ.get("TUI_MUSIC_FOLDERS")
    if override:
        return Path(override).expanduser()
    return data_dir() / "folders.json"


def analysis_log_path() -> Path:
    override = os.environ.get("TUI_MUSIC_LOG")
    if override:
        return Path(override).expanduser()
    return data_dir() / "analysis.log"


def analysis_state_path() -> Path:
    override = os.environ.get("TUI_MUSIC_ANALYSIS_STATE")
    if override:
        return Path(override).expanduser()
    return data_dir() / "analysis_state.json"
