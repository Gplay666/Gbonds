from __future__ import annotations

import atexit
import os
import signal
import sys
from typing import Any, Optional

from rich.console import Group
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.content import Content
from textual.css.query import NoMatches
from textual.message import Message
from textual.screen import ModalScreen, Screen
from textual.widget import Widget
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
    TabbedContent,
    TabPane,
)
from analysis_tracker import (
    format_analysis_state,
    format_duration,
    read_analysis_state,
)
from audio_analysis import format_analysis
from folder_store import (
    FolderStore,
    display_folder,
    normalize_folder,
    path_is_within,
    tracks_in_folder,
)
from folder_watcher import (
    FolderChange,
    collect_snapshot,
    diff_snapshots,
    library_snapshot,
)
from main import MusicSystem
from playback import PlaybackError, PlayerController, PlayerError, PlayerStatus
from playlist_store import PlaylistStore
from track import Track


def fmt_time(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    return f"{seconds // 60}:{seconds % 60:02d}"


def rich_color(value: Optional[str], fallback: str = "default") -> str:
    """ANSI-имена Textual → имена, которые понимает Rich Text."""
    if not value:
        return fallback
    if value.startswith("ansi_"):
        return value[5:]
    return value


def watch_interval_seconds() -> float:
    raw = os.environ.get("TUI_MUSIC_WATCH_INTERVAL", "5")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        value = 5.0
    return max(1.0, value)


def _id_sort_key(track_id) -> tuple:
    try:
        return (0, int(track_id))
    except (TypeError, ValueError):
        return (1, str(track_id))


# Material Design shuffle from Nerd Font: прямые линии, чёткий перекрест.
SHUFFLE_ICON = "\U000f049d"
REPEAT_ICON = "⇄"


class PlayerBar(Widget):
    """Две строки: текущий трек и прогресс/режимы плеера."""

    can_focus = False
    DEFAULT_CSS = """
    PlayerBar {
        height: 3;
        padding: 0 1;
        border-top: solid $secondary;
        background: transparent;
    }
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._status: Optional[PlayerStatus] = None
        self._track = None

    def update_status(self, status: PlayerStatus, track) -> None:
        self._status = status
        self._track = track
        self.refresh()

    def _styles(self):
        theme = getattr(self.app, "current_theme", None)
        accent = rich_color(getattr(theme, "accent", None), "green")
        primary = rich_color(getattr(theme, "primary", None), "blue")
        error = rich_color(getattr(theme, "error", None), "red")
        foreground = rich_color(getattr(theme, "foreground", None), "default")
        return {
            "accent": accent,
            "primary": primary,
            "error": error,
            "fg": foreground,
            "muted": f"{foreground} dim",
        }

    def render(self) -> Group:
        styles = self._styles()
        width = max(20, self.content_size.width or 80)
        status = self._status
        track = self._track

        line1 = Text()
        line2 = Text()

        if status is None or track is None:
            line1.append("■ ", style=styles["muted"])
            line1.append("ничего не играет", style=styles["muted"])
            line2.append("space", style=f"bold {styles['accent']}")
            line2.append(" · play/pause · ", style=styles["muted"])
            line2.append("e", style=f"bold {styles['accent']}")
            line2.append(" · в очередь", style=styles["muted"])
            if status and status.queue:
                line2.append(
                    f" · queue {len(status.queue)}", style=styles["muted"]
                )
            line1.truncate(width, overflow="ellipsis")
            line2.truncate(width, overflow="ellipsis")
            return Group(line1, line2)

        state = status.state
        glyph = {
            "playing": "▶",
            "paused": "❚❚",
            "stopped": "■",
            "error": "!",
        }.get(state, "·")
        state_style = styles["error"] if state == "error" else styles["accent"]
        line1.append(f"{glyph} ", style=f"bold {state_style}")
        line1.append(track.artist or "Unknown", style=f"bold {styles['primary']}")
        line1.append(" · ", style=styles["muted"])
        line1.append(track.title or "Unknown", style=f"bold {styles['fg']}")
        if track.album:
            line1.append(" · ", style=styles["muted"])
            line1.append(track.album, style=styles["muted"])
        if state == "paused":
            line1.append(" · pause", style=f"{styles['accent']} dim")
        elif state == "error":
            line1.append(" · error", style=f"{styles['error']} bold")
        elif state == "stopped" and status.queue:
            line1.append(" · stopped", style=styles["muted"])

        duration = status.duration or float(track.duration or 0)
        ratio = 0.0 if duration <= 0 else max(0.0, min(1.0, status.position / duration))
        compact = width < 100
        suffix_width = 43 if not compact else 45
        bar_width = max(6, min(32 if compact else 34, width - suffix_width))
        filled = int(round(bar_width * ratio))
        line2.append("━" * filled, style=f"{styles['accent']} bold")
        line2.append("─" * (bar_width - filled), style=styles["muted"])
        if compact:
            line2.append("  ", style=styles["muted"])
            line2.append(fmt_time(status.position), style=styles["muted"])
            line2.append("/", style=styles["muted"])
            line2.append(fmt_time(duration), style=styles["muted"])
            line2.append(f" · {status.volume}%", style=styles["accent"])
            if status.shuffle:
                line2.append(" · shuffle", style=styles["accent"])
            if status.repeat != "off":
                line2.append(f" · loop:{status.repeat}", style=styles["accent"])
            if status.queue:
                line2.append(
                    f" · {status.queue_index + 1}/{len(status.queue)}",
                    style=styles["muted"],
                )
        else:
            line2.append("  ", style=styles["muted"])
            line2.append(fmt_time(status.position), style=styles["muted"])
            line2.append(" / ", style=styles["muted"])
            line2.append(fmt_time(duration), style=styles["muted"])
            line2.append(f" · vol {status.volume}%", style=styles["accent"])
            if status.shuffle:
                line2.append(" · shuffle", style=styles["accent"])
            if status.repeat != "off":
                line2.append(f" · loop:{status.repeat}", style=styles["accent"])
            if status.queue:
                line2.append(
                    f" · {status.queue_index + 1}/{len(status.queue)}",
                    style=styles["muted"],
                )

        line1.truncate(width, overflow="ellipsis")
        line2.truncate(width, overflow="ellipsis")
        return Group(line1, line2)


class NotificationLine(Static):
    """Одна нижняя строка для текстовых уведомлений."""

    can_focus = False

    def on_mount(self) -> None:
        sync = getattr(self.app, "_sync_notification_line", None)
        if sync is not None:
            sync(self)


class NotificationScreenMixin:
    """Добавляет строку уведомлений на каждый экран, включая модальные."""

    def _extend_compose(self, widgets: list[Widget]) -> None:
        super()._extend_compose(widgets)
        widgets.append(NotificationLine(id="notification_line"))


class MainScreen(NotificationScreenMixin, Screen):
    pass


class NotificationModalScreen(NotificationScreenMixin, ModalScreen):
    pass


class PromptScreen(NotificationModalScreen):
    """Универсальный запрос одной строки."""

    BINDINGS = [Binding("escape", "cancel", "Отмена")]

    def __init__(self, title: str, placeholder: str = "", value: str = ""):
        super().__init__()
        self.title = title
        self.placeholder = placeholder
        self.value = value

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self.title, classes="dialog-title"),
            Horizontal(
                Label(self.placeholder or "Значение", classes="field-label"),
                Input(
                    value=self.value,
                    placeholder=self.placeholder,
                    id="value",
                    compact=True,
                ),
                classes="field-row",
            ),
            Horizontal(
                Button("Готово", id="ok", variant="primary"),
                Button("Отмена", id="cancel"),
                classes="buttons",
            ),
            classes="dialog prompt-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#value", Input).focus()

    def _submit(self) -> None:
        value = self.query_one("#value", Input).value.strip()
        self.dismiss(value or None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self._submit()
        elif event.button.id == "cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmScreen(NotificationModalScreen):
    BINDINGS = [
        Binding("y", "confirm", "Да"),
        Binding("n", "cancel", "Нет"),
        Binding("escape", "cancel", "Отмена"),
    ]

    def __init__(self, message: str, title: str = "Подтверждение"):
        super().__init__()
        self.message = message
        self.title = title

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(self.title, classes="dialog-title"),
            Static(self.message, classes="dialog-message"),
            Horizontal(
                Button("Да", id="yes", variant="error"),
                Button("Нет", id="no", variant="primary"),
                classes="buttons",
            ),
            classes="dialog confirm-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class PlayAllScreen(NotificationModalScreen):
    """Выбор стартовой точки для проигрывания всей библиотеки."""

    BINDINGS = [
        Binding("f", "first", "С начала"),
        Binding("r", "random", "Случайный"),
        Binding("escape", "cancel", "Отмена"),
    ]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Играть всю библиотеку", classes="dialog-title"),
            Static(
                "Сначала — по порядку библиотеки. "
                "Случайный — перемешать все треки.",
                classes="dialog-message",
            ),
            Horizontal(
                Button("С НАЧАЛА", id="first", variant="primary"),
                Button("С СЛУЧАЙНОГО", id="random"),
                Button("Отмена", id="cancel"),
                classes="buttons",
            ),
            classes="dialog play-all-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id in ("first", "random"):
            self.dismiss(event.button.id)
        else:
            self.dismiss(None)

    def action_first(self) -> None:
        self.dismiss("first")

    def action_random(self) -> None:
        self.dismiss("random")

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddRelationScreen(NotificationModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Отмена")]

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Добавить связь", classes="dialog-title"),
            Horizontal(
                Label("Трек 1", classes="field-label"),
                Input(placeholder="ID первого трека", id="track1", compact=True),
                classes="field-row",
            ),
            Horizontal(
                Label("Трек 2", classes="field-label"),
                Input(placeholder="ID второго трека", id="track2", compact=True),
                classes="field-row",
            ),
            Horizontal(
                Label("Вес", classes="field-label"),
                Input(placeholder="Вес связи", id="weight", value="1", compact=True),
                classes="field-row",
            ),
            Horizontal(
                Button("Добавить", id="submit", variant="primary"),
                Button("Отмена", id="cancel"),
                classes="buttons",
            ),
            classes="dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#track1", Input).focus()

    def _submit(self) -> None:
        try:
            data = {
                "id1": self.query_one("#track1", Input).value.strip(),
                "id2": self.query_one("#track2", Input).value.strip(),
                "weight": float(self.query_one("#weight", Input).value),
            }
        except ValueError:
            self.notify("Вес должен быть числом", severity="error")
            return
        if not data["id1"] or not data["id2"]:
            self.notify("Укажите ID обоих треков", severity="error")
            return
        self.dismiss(data)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)


class AddTrackScreen(NotificationModalScreen):
    BINDINGS = [Binding("escape", "cancel", "Отмена")]

    def compose(self) -> ComposeResult:
        fields = (
            ("ID", "track_id", ""),
            ("Название", "title", ""),
            ("Исполнитель", "artist", ""),
            ("Жанр", "genre", ""),
            ("Длительность", "duration", ""),
            ("Рейтинг", "rating", ""),
        )
        rows = [
            Horizontal(
                Label(label, classes="field-label"),
                Input(
                    placeholder=label,
                    id=field_id,
                    value=value,
                    compact=True,
                ),
                classes="field-row",
            )
            for label, field_id, value in fields
        ]
        yield Vertical(
            Label("Добавить трек", classes="dialog-title"),
            *rows,
            Horizontal(
                Button("Добавить", id="submit", variant="primary"),
                Button("Отмена", id="cancel"),
                classes="buttons",
            ),
            classes="dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#track_id", Input).focus()

    def _submit(self) -> None:
        track_id = self.query_one("#track_id", Input).value.strip()
        title = self.query_one("#title", Input).value.strip()
        if not track_id or not title:
            self.notify("ID и название обязательны", severity="error")
            return
        try:
            track = Track(
                track_id,
                title,
                self.query_one("#artist", Input).value.strip(),
                self.query_one("#genre", Input).value.strip() or "Unknown",
                int(self.query_one("#duration", Input).value or 0),
                int(self.query_one("#rating", Input).value or 5),
            )
        except ValueError:
            self.notify("Длительность и рейтинг должны быть числами", severity="error")
            return
        self.dismiss(track)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._submit()

    def action_cancel(self) -> None:
        self.dismiss(None)


class ReadMeScreen(NotificationModalScreen):
    BINDINGS = [
        Binding("escape", "close", "Закрыть"),
        Binding("f1", "close", "Закрыть"),
    ]

    HELP = """[bold]Gbonds[/]
TUI PLAYER WITH LOCAL RECOMENDATIONS

[bold $accent]PLAYER[/]
  Нижняя панель  кнопки транспорта, очередь и тема
  space          play / pause
  enter          играть выбранное (трек, рекомендацию, очередь, плейлист)
  x              stop
  n / b          следующий / предыдущий
  ctrl+left      -5 секунд
  ctrl+right     +5 секунд
  + / -          громкость
  s              shuffle on/off
  S              перемешать очередь
  l              loop: off → all → one
  e              добавить выбранное в очередь
  E              добавить рекомендации в очередь
  R              радио по выбранному треку
  ВСЁ / A        вся библиотека: с начала или случайно
  u              показать очередь
  Delete         убрать выбранное из очереди
  ctrl+up/down   переместить элемент очереди
  ctrl+k         очистить очередь

[bold $accent]БИБЛИОТЕКА И РЕКОМЕНДАЦИИ[/]
  Двойной клик    играть; если трека нет в очереди — вся библиотека
  Верхняя панель   кнопки папок, треков, связей, анализа и плейлистов
  Боковые панели   кнопки рекомендаций, очереди и плейлистов
  Импорт         сверху видно этап, сделано/всего и время
  Статус снаружи dist/music_tui --status
  /              фильтр по библиотеке
  r              рекомендации для выбранного трека
  i              подробный аудио-анализ
  p              вкладка плейлистов
  P              менеджер плейлистов
  a / d          добавить / удалить трек
  c              добавить связь между треками
  o / ПАПКИ      папки, общий граф и автослежение за файлами
  ctrl+s         сохранить библиотеку и граф
  f1 / y         эта справка
  f2             переключить ANSI-тему
  q              выход

[dim]Библиотека собирается из добавленных папок. Удаление папки
убирает только записи каталога, аудиофайлы остаются на диске.[/]
"""

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Справка", classes="dialog-title"),
            VerticalScroll(Static(self.HELP, classes="help-text")),
            Horizontal(
                Button("Закрыть", id="close", variant="primary"),
                classes="buttons",
            ),
            classes="dialog help-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()

    def action_close(self) -> None:
        self.dismiss()


class AnalysisScreen(NotificationModalScreen):
    BINDINGS = [Binding("escape", "close", "Закрыть")]

    def __init__(self, track: Track):
        super().__init__()
        self.track = track

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(
                f"{self.track.artist} — {self.track.title}",
                classes="dialog-title",
            ),
            Static(
                f"Альбом: {self.track.album or '—'}   ·   "
                f"Жанр: {self.track.genre or '—'}   ·   "
                f"Длительность: {fmt_time(self.track.duration)}",
                classes="dialog-message",
            ),
            VerticalScroll(
                Static(format_analysis(self.track), classes="analysis-text")
            ),
            Horizontal(
                Button("Закрыть", id="close", variant="primary"),
                classes="buttons",
            ),
            classes="dialog analysis-dialog",
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()

    def action_close(self) -> None:
        self.dismiss()


class FolderManagerScreen(NotificationModalScreen):
    """Меню музыкальных папок и общей библиотеки."""

    BINDINGS = [Binding("escape", "close", "Закрыть")]

    def __init__(self):
        super().__init__()
        self.folders_table = DataTable(id="folders_table")
        self.path_input = Input(
            placeholder="/home/user/Music",
            id="folder_path",
            compact=True,
        )
        self._row_paths: list[str] = []

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Музыкальные папки", classes="dialog-title"),
            Static(
                "Библиотека собирается из этих папок в один общий граф. "
                "Аудиофайлы не копируются и не изменяются. "
                "Действия применяются к выделенной папке.",
                classes="dialog-message",
            ),
            self.folders_table,
            Horizontal(
                Label("Путь", classes="field-label"),
                self.path_input,
                classes="field-row",
            ),
            Horizontal(
                Button("Добавить папку", id="add", variant="primary"),
                Button("Пересканировать", id="scan"),
                Button("Убрать папку", id="remove", variant="error"),
                classes="buttons folder-actions",
            ),
            Horizontal(
                Button("Слежение: вкл", id="watch", compact=True),
                Button("Закрыть", id="close", compact=True),
                classes="buttons watch-row",
            ),
            classes="dialog folder-dialog",
        )

    def on_mount(self) -> None:
        path_width = max(24, min(70, self.app.size.width - 36))
        self.folders_table.add_column("ПАПКА", width=path_width)
        self.folders_table.add_column("ТРЕКОВ", width=7)
        self.folders_table.cursor_type = "row"
        self.folders_table.zebra_stripes = False
        self._load_folders()
        self._update_watch_button()
        self.path_input.focus()

    def _update_watch_button(self) -> None:
        button = self.query_one("#watch", Button)
        enabled = bool(self.app.folders.watch_enabled)
        button.label = "Слежение: вкл" if enabled else "Слежение: выкл"
        button.variant = "primary" if enabled else "default"

    def _load_folders(self) -> None:
        current = self._selected_path()
        current_index = 0
        if current in self._row_paths:
            current_index = self._row_paths.index(current)
        self.folders_table.clear()
        self._row_paths = []
        tracks = list(self.app.system.table.values())
        for path in self.app.folders.names():
            display = display_folder(path)
            if not os.path.isdir(path):
                display += " [нет]"
            count = len(tracks_in_folder(tracks, path))
            self._row_paths.append(path)
            self.folders_table.add_row(display, str(count), key=path)
        if self.folders_table.row_count:
            self.folders_table.move_cursor(
                row=min(current_index, self.folders_table.row_count - 1)
            )

    def _selected_path(self) -> Optional[str]:
        index = self.folders_table.cursor_row
        if 0 <= index < len(self._row_paths):
            return self._row_paths[index]
        return None

    def _add(self) -> None:
        raw = self.path_input.value.strip()
        if not raw:
            self.notify("Введите путь к папке", severity="error")
            return
        path = normalize_folder(raw)
        if not os.path.isdir(path):
            self.notify("Папка не найдена", severity="error")
            return
        if path in self.app.folders:
            self.notify("Эта папка уже добавлена", severity="warning")
            return
        self.dismiss({"action": "add", "path": path})

    def _scan(self) -> None:
        path = self._selected_path()
        if not path:
            self.notify("Сначала выберите папку в списке", severity="warning")
            return
        self.dismiss({"action": "scan", "path": path})

    def _remove(self) -> None:
        path = self._selected_path()
        if not path:
            self.notify("Сначала выберите папку в списке", severity="warning")
            return
        self.app.push_screen(
            ConfirmScreen(
                "Убрать папку и все её треки из библиотеки?\n"
                f"{display_folder(path)}\n"
                "Аудиофайлы на диске останутся на месте.",
                "Убрать папку",
            ),
            lambda confirmed: self._confirmed_remove(path, confirmed),
        )

    def _confirmed_remove(self, path: str, confirmed: bool) -> None:
        if confirmed:
            self.dismiss({"action": "remove", "path": path})

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "add":
            self._add()
        elif event.button.id == "scan":
            self._scan()
        elif event.button.id == "remove":
            self._remove()
        elif event.button.id == "watch":
            self.dismiss(
                {
                    "action": "toggle_watch",
                    "enabled": not self.app.folders.watch_enabled,
                }
            )
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._add()

    def action_close(self) -> None:
        self.dismiss(None)


class PlaylistManagerScreen(NotificationModalScreen):
    BINDINGS = [
        Binding("escape", "close", "Закрыть"),
        Binding("n", "new_playlist", "Новый"),
        Binding("a", "add_track", "Добавить трек"),
        Binding("delete", "remove_track", "Убрать трек"),
        Binding("enter", "play_playlist", "Играть"),
        Binding("ctrl+d", "delete_playlist", "Удалить плейлист"),
    ]

    def __init__(self, initial_track_id: Optional[str] = None):
        super().__init__()
        self.initial_track_id = initial_track_id
        self.playlists_table = DataTable(id="playlists_table")
        self.tracks_table = DataTable(id="playlist_tracks")

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Плейлисты", classes="dialog-title"),
            Horizontal(
                Vertical(
                    Static("СПИСОК", classes="subsection-title"),
                    self.playlists_table,
                    Static("n · ^D · Tab → треки", classes="pane-hint"),
                    classes="playlist-column",
                ),
                Vertical(
                    Static("ТРЕКИ", classes="subsection-title"),
                    self.tracks_table,
                    Static(
                        "a + · Del − · Enter",
                        classes="pane-hint",
                    ),
                    classes="playlist-column",
                ),
                classes="playlist-layout",
            ),
            Horizontal(
                Button("Новый", id="new", variant="primary"),
                Button("Добавить трек", id="add"),
                Button("Играть", id="play"),
                Button("Удалить", id="delete-playlist", variant="error"),
                Button("Закрыть", id="close"),
                classes="buttons",
            ),
            classes="dialog playlist-dialog",
        )

    def on_mount(self) -> None:
        compact = self.app.size.width < 100
        self.playlists_table.add_column(
            "НАЗВАНИЕ", width=13 if compact else 22
        )
        self.playlists_table.add_column("ТРЕКОВ", width=4 if compact else 7)
        if compact:
            self.tracks_table.add_column("#", width=2)
            self.tracks_table.add_column("ТРЕК", width=16)
            self.tracks_table.add_column("ДЛИТ.", width=4)
        else:
            self.tracks_table.add_column("#", width=2)
            self.tracks_table.add_column("ID", width=4)
            self.tracks_table.add_column("АРТИСТ", width=10)
            self.tracks_table.add_column("ТРЕК", width=18)
            self.tracks_table.add_column("ДЛИТ.", width=4)
        self._compact_tracks = compact
        self.playlists_table.cursor_type = "row"
        self.tracks_table.cursor_type = "row"
        self.playlists_table.zebra_stripes = False
        self.tracks_table.zebra_stripes = False
        self._load_playlists()
        self.playlists_table.focus()

    def _load_playlists(self) -> None:
        current = self._selected_playlist()
        self.playlists_table.clear()
        for name in self.app.playlists.names():
            self.playlists_table.add_row(
                name,
                str(self.app.playlists.count(name)),
                key=name,
            )
        if self.playlists_table.row_count:
            try:
                if current:
                    self.playlists_table.move_cursor(
                        row=self.playlists_table.get_row_index(current)
                    )
                else:
                    self.playlists_table.move_cursor(row=0)
            except Exception:
                self.playlists_table.move_cursor(row=0)
        self._load_tracks()

    def _selected_playlist(self) -> Optional[str]:
        if not self.playlists_table.row_count:
            return None
        try:
            row = self.playlists_table.get_row_at(self.playlists_table.cursor_row)
        except Exception:
            return None
        return str(row[0]) if row else None

    def _load_tracks(self) -> None:
        name = self._selected_playlist()
        current_index = self.tracks_table.cursor_row if self.tracks_table.row_count else 0
        self.tracks_table.clear()
        if not name:
            return
        for index, track_id in enumerate(self.app.playlists.get(name), start=1):
            track = self.app.system.find_track(track_id)
            if track is None:
                artist, title, duration = "?", f"[missing {track_id}]", "—"
            else:
                artist, title = track.artist, track.title
                duration = fmt_time(track.duration)
            if getattr(self, "_compact_tracks", False):
                cells = (str(index), title, duration)
            else:
                cells = (str(index), str(track_id), artist, title, duration)
            self.tracks_table.add_row(
                *cells,
                key=f"{name}:{index - 1}",
            )
        if self.tracks_table.row_count:
            try:
                self.tracks_table.move_cursor(
                    row=min(current_index, self.tracks_table.row_count - 1)
                )
            except Exception:
                self.tracks_table.move_cursor(row=0)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table is self.playlists_table:
            self._load_tracks()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table in (self.playlists_table, self.tracks_table):
            self.action_play_playlist()

    def _selected_track_index(self) -> Optional[int]:
        if not self.tracks_table.row_count:
            return None
        try:
            row = self.tracks_table.get_row_at(self.tracks_table.cursor_row)
        except Exception:
            return None
        try:
            return int(row[0]) - 1
        except (TypeError, ValueError):
            return None

    def _create_playlist(self, name: Optional[str]) -> None:
        if not name:
            return
        try:
            self.app.playlists.create(name)
        except Exception as exc:
            self.notify(str(exc), severity="error")
            return
        self._load_playlists()
        self.notify(f"Плейлист «{name}» создан")

    def action_new_playlist(self) -> None:
        self.app.push_screen(
            PromptScreen("Новый плейлист", "Название"),
            self._create_playlist,
        )

    def action_add_track(self) -> None:
        name = self._selected_playlist()
        if not name:
            self.notify("Сначала создайте или выберите плейлист", severity="error")
            return
        track_id = self.initial_track_id or self.app._active_track_id()
        if not track_id:
            self.notify("В библиотеке не выбран трек", severity="error")
            return
        self.app.playlists.add(name, [track_id])
        self._load_playlists()
        self.app._refresh_playlists()
        self.notify(f"Трек добавлен в «{name}»")

    def action_remove_track(self) -> None:
        if not self.tracks_table.has_focus:
            self.notify("Сначала выберите трек в правой таблице", severity="warning")
            return
        name = self._selected_playlist()
        index = self._selected_track_index()
        if not name or index is None:
            self.notify("Не выбран трек в плейлисте", severity="error")
            return
        self.app.playlists.remove_at(name, index)
        self._load_playlists()
        self.app._refresh_playlists()

    def action_play_playlist(self) -> None:
        name = self._selected_playlist()
        if not name:
            self.notify("Плейлист не выбран", severity="error")
            return
        ids = self.app.playlists.get(name)
        if not ids:
            self.notify("Плейлист пуст", severity="error")
            return
        try:
            self.app.player.set_queue(ids, autoplay=True)
        except (PlayerError, PlaybackError) as exc:
            self.notify(str(exc), severity="error", timeout=8)
            return
        self.app._sync_player()
        self.app.notify(f"Играю плейлист «{name}» ({len(ids)} треков)")
        self.dismiss()

    def _delete_confirmed(self, confirmed: bool) -> None:
        if not confirmed:
            return
        name = self._selected_playlist()
        if name and self.app.playlists.delete(name):
            self._load_playlists()
            self.app._refresh_playlists()
            self.notify(f"Плейлист «{name}» удалён")

    def action_delete_playlist(self) -> None:
        name = self._selected_playlist()
        if not name:
            self.notify("Плейлист не выбран", severity="error")
            return
        self.app.push_screen(
            ConfirmScreen(f"Удалить плейлист «{name}»?", "Удаление плейлиста"),
            self._delete_confirmed,
        )

    def action_close(self) -> None:
        self.dismiss()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "new": self.action_new_playlist,
            "add": self.action_add_track,
            "play": self.action_play_playlist,
            "delete-playlist": self.action_delete_playlist,
            "close": self.action_close,
        }
        action = mapping.get(event.button.id)
        if action:
            action()


class StatusNotice(Message):
    """Уведомление для нижней текстовой строки."""

    def __init__(
        self,
        message: str,
        severity: str = "information",
        timeout: Optional[float] = None,
    ) -> None:
        super().__init__()
        self.message = message
        self.severity = severity
        self.timeout = timeout


class MusicApp(App):
    """ANSI-rice TUI: библиотека, рекомендации, очередь и плейлисты."""

    TITLE = "Gbond"
    SUB_TITLE = "TUI PLAYER WITH LOCAL RECOMENDATIONS"

    CSS = """
    /* Цвета ниже - только системные ANSI-цвета из темы.
       surface/panel в ansi-dark и ansi-light прозрачные. */
    Screen {
        background: transparent;
        color: $text;
        layout: vertical;
    }

    /* Текстовая строка уведомлений в самом низу экрана. */
    #notification_line {
        dock: bottom;
        width: 100%;
        height: 1;
        padding: 0 1;
        color: $text;
        background: transparent;
        text-wrap: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    #notification_line.-warning {
        color: $warning;
    }
    #notification_line.-error {
        color: $error;
    }

    Header {
        background: transparent;
        color: $text-muted;
        height: 1;
        padding: 0 1;
    }
    Header HeaderIcon {
        color: $accent;
        background: transparent;
    }
    Header HeaderTitle {
        color: $primary;
        background: transparent;
        text-style: bold;
    }
    Header HeaderClock {
        color: $accent;
        background: transparent;
    }

    #stats {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: transparent;
    }

    #main_toolbar {
        height: 1;
        padding: 0 1;
        background: transparent;
    }
    #player_controls {
        height: 2;
        padding: 0 1;
        background: transparent;
    }
    .player-row {
        height: 1;
    }
    #rec_actions,
    #playlist_actions {
        height: 1;
        padding: 0 1;
        background: transparent;
    }
    #rec_actions {
        height: 2;
    }
    #queue_actions {
        height: 2;
        padding: 0 1;
        background: transparent;
    }
    .action-row {
        height: 1;
    }
    #main_toolbar Button,
    #player_controls Button,
    #rec_actions Button,
    #playlist_actions Button,
    .action-row Button {
        width: auto;
        min-width: 0;
        height: 1;
        min-height: 1;
        border: none;
        background: transparent;
        color: $text-muted;
        text-style: bold;
    }
    #main_toolbar Button,
    #player_controls Button {
        padding: 0 1;
        margin-right: 1;
    }
    #player_controls Button {
        width: 5;
        min-width: 5;
        padding: 0;
        text-align: center;
    }
    #rec_actions Button,
    #playlist_actions Button,
    .action-row Button {
        padding: 0;
        margin-right: 1;
    }
    #main_toolbar Button:hover,
    #player_controls Button:hover,
    #rec_actions Button:hover,
    #playlist_actions Button:hover,
    .action-row Button:hover {
        color: $accent;
        background: $panel;
    }
    #main_toolbar Button.-primary {
        color: $accent;
        border: none;
        background: transparent;
    }
    #main_toolbar Button.-primary:hover {
        color: $text;
        background: $panel;
    }
    #main_toolbar Button:focus,
    #player_controls Button:focus,
    #rec_actions Button:focus,
    #playlist_actions Button:focus,
    .action-row Button:focus {
        color: $background;
        background: $primary;
    }

    #body {
        height: 1fr;
        padding: 0 1;
        background: transparent;
    }

    #library_panel {
        width: 2.15fr;
        height: 1fr;
        margin-right: 1;
        border: round $secondary;
        background: transparent;
    }

    #side_panel {
        width: 1.15fr;
        height: 1fr;
        border: round $secondary;
        background: transparent;
    }

    .panel-title {
        color: $primary;
        text-style: bold;
        height: 1;
        padding: 0 1;
    }

    #library_header {
        height: 1;
        padding: 0 1;
    }

    #library_title {
        width: 1fr;
        height: 1;
        color: $primary;
        text-style: bold;
        content-align: left middle;
    }

    #filter {
        width: 22;
        height: 1;
        border: none;
        background: transparent;
        color: $primary;
        padding: 0 1;
    }
    #filter:focus {
        color: $accent;
        background: transparent;
    }
    #filter .input--placeholder {
        color: $text-disabled;
        text-style: dim;
    }
    #library_header Button {
        width: 3;
        min-width: 0;
        height: 1;
        min-height: 1;
        padding: 0;
        border: none;
        background: transparent;
        color: $text-muted;
        text-style: bold;
    }
    #library_header Button:hover {
        color: $accent;
        background: $panel;
    }
    #library_header Button:focus {
        color: $background;
        background: $primary;
    }

    DataTable {
        background: transparent;
        color: $text;
        border: none;
        padding: 0 1;
        scrollbar-size-horizontal: 0;
    }
    DataTable:focus {
        background-tint: transparent;
    }
    DataTable > .datatable--header,
    DataTable:ansi > .datatable--header {
        color: $primary;
        background: transparent;
        text-style: bold;
    }
    DataTable:focus > .datatable--header {
        background-tint: transparent;
    }
    DataTable > .datatable--header-hover,
    DataTable:ansi > .datatable--header-hover {
        color: $accent;
        background: transparent;
    }
    DataTable > .datatable--header-cursor {
        color: $accent;
        background: transparent;
        text-style: bold reverse;
    }
    DataTable > .datatable--cursor {
        background: ansi_bright_black;
        color: ansi_bright_white;
        text-style: bold;
    }
    DataTable:focus > .datatable--cursor {
        background: $primary;
        color: ansi_bright_white;
        text-style: bold;
    }
    DataTable > .datatable--hover {
        text-style: underline;
    }
    DataTable > .datatable--even-row,
    DataTable > .datatable--odd-row,
    DataTable > .datatable--fixed {
        background: transparent;
    }

    #side-tabs {
        height: 1fr;
        background: transparent;
    }
    #side-tabs Tabs {
        height: 2;
        background: transparent;
    }
    #side-tabs Tab {
        color: $text-muted;
        background: transparent;
        text-style: dim;
        padding: 0 1;
    }
    #side-tabs Tab:hover {
        color: $text;
    }
    #side-tabs Tab.-active {
        color: $accent;
        text-style: bold;
    }
    #side-tabs Tabs .underline--bar {
        background: $secondary;
    }
    #side-tabs Tabs:focus .underline--bar {
        background: $accent;
    }
    #side-tabs ContentSwitcher {
        height: 1fr;
        background: transparent;
    }
    #side-tabs TabPane {
        height: 1fr;
        padding: 0;
        background: transparent;
    }

    #recommendations,
    #queue_table,
    #playlists_table {
        height: 1fr;
    }

    .pane-hint {
        height: 1;
        width: 100%;
        color: $text-muted;
        text-style: dim;
        padding: 0 1;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    Footer {
        dock: none;
        background: transparent;
        color: $text-muted;
        height: 1;
    }
    Footer FooterKey {
        background: transparent;
    }
    Footer FooterKey .footer-key--key {
        color: $primary;
        background: transparent;
        text-style: bold;
    }
    Footer FooterKey .footer-key--description {
        color: $text-muted;
        background: transparent;
    }
    Footer FooterLabel {
        color: $text-muted;
        background: transparent;
    }

    ModalScreen {
        align: center middle;
        background: $background;
    }

    .dialog {
        width: 68;
        max-width: 92%;
        height: auto;
        max-height: 86%;
        padding: 1 2;
        border: round $accent;
        background: transparent;
    }
    .dialog-title {
        color: $primary;
        text-style: bold;
        height: 1;
        margin-bottom: 1;
    }
    .dialog-message {
        color: $text-muted;
        margin-bottom: 1;
    }
    .dialog Input {
        background: transparent;
        border: none;
        height: 1;
        padding: 0 1;
        color: $text;
        margin-bottom: 0;
    }
    .dialog Input:focus {
        color: $accent;
    }
    .field-row {
        height: 1;
        margin-bottom: 1;
    }
    .field-label {
        width: 14;
        height: 1;
        padding: 0 1;
        color: $text-muted;
    }
    .field-row Input {
        width: 1fr;
        height: 1;
        margin-bottom: 0;
    }
    .dialog Button {
        min-width: 12;
        margin-left: 1;
        border: round $border;
        background: transparent;
        color: $text;
    }
    .dialog Button:hover {
        border: round $accent;
        color: $accent;
        background: transparent;
    }
    .dialog Button.-primary {
        border: round $accent;
        color: $accent;
    }
    .buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    .prompt-dialog {
        height: auto;
    }
    .confirm-dialog {
        width: 54;
    }
    .help-dialog {
        width: 86%;
        height: 86%;
    }
    .help-text {
        color: $text;
        height: auto;
    }
    .analysis-dialog {
        width: 86%;
        height: 86%;
    }
    .analysis-text {
        color: $text;
        height: auto;
    }
    .analysis-dialog VerticalScroll {
        height: 1fr;
        margin: 1 0;
    }
    .folder-dialog {
        width: 92%;
        height: 88%;
        max-height: 88%;
    }
    .folder-actions Button {
        min-width: 0;
    }
    .watch-row {
        height: 1;
        margin-top: 0;
    }
    .watch-row Button {
        height: 1;
        min-height: 1;
        border: none;
        padding: 0 1;
        color: $accent;
        background: transparent;
    }
    .watch-row Button.-primary,
    .watch-row Button.-primary:hover {
        height: 1;
        min-height: 1;
        border: none;
        padding: 0 1;
        background: transparent;
    }
    .watch-row Button:hover {
        color: $text;
        background: $surface;
        border: none;
    }
    .folder-dialog DataTable {
        height: 1fr;
        min-height: 4;
    }
    .playlist-dialog {
        width: 92%;
        height: 84%;
    }
    .playlist-layout {
        height: 1fr;
    }
    .playlist-column {
        width: 1fr;
        height: 1fr;
        padding: 0 1;
    }
    .playlist-column:first-child {
        margin-right: 1;
    }
    .subsection-title {
        color: $secondary;
        text-style: bold;
        height: 1;
    }

    """

    BINDINGS = [
        Binding("space", "play_pause", "играть", show=True),
        Binding("enter", "play_selected", "play", show=True),
        Binding("e", "enqueue", "в очередь", show=True),
        Binding("s", "toggle_shuffle", "shuffle", show=False),
        Binding("S", "shuffle_queue", "перемешать очередь", show=False),
        Binding("l", "cycle_repeat", "loop", show=False),
        Binding("u", "open_queue", "очередь", show=False),
        Binding("p", "open_playlists", "плейлисты", show=False),
        Binding("r", "show_recommendations", "рекомендации", show=True),
        Binding("R", "radio", "радио", show=False),
        Binding("A", "play_all", "вся библиотека", show=False),
        Binding("i", "show_info", "анализ", show=False),
        Binding("x", "stop", "stop", show=False),
        Binding("n", "next_track", "next", show=False),
        Binding("b", "previous_track", "prev", show=False),
        Binding("ctrl+left", "seek_back", "−5s", show=False),
        Binding("ctrl+right", "seek_forward", "+5s", show=False),
        Binding("plus", "volume_up", "vol+", show=False),
        Binding("minus", "volume_down", "vol−", show=False),
        Binding("delete", "remove_from_queue", "убрать из очереди", show=False),
        Binding("ctrl+up", "move_queue_up", "выше", show=False),
        Binding("ctrl+down", "move_queue_down", "ниже", show=False),
        Binding("ctrl+k", "clear_queue", "очистить очередь", show=False),
        Binding("E", "enqueue_recommendations", "рекомендации в очередь", show=False),
        Binding("P", "manage_playlists", "менеджер плейлистов", show=False),
        Binding("slash", "focus_filter", "поиск", show=False),
        Binding("escape", "clear_filter", "сброс фильтра", show=False),
        Binding("a", "add_track", "add", show=False),
        Binding("d", "delete_track", "delete", show=False),
        Binding("c", "connect_tracks", "connect", show=False),
        Binding("ctrl+s", "save_library", "save", show=False),
        Binding("o", "manage_folders", "папки", show=True),
        Binding("f1", "show_readme", "help", show=True),
        Binding("y", "show_readme", "help", show=False),
        Binding("f2", "toggle_theme", "theme", show=False),
        Binding("q", "quit", "quit", show=True),
        Binding("ctrl+q", "quit", "quit", show=False),
    ]

    def __init__(self):
        super().__init__()
        # Textual toasts отключены: уведомления выводятся в нижнюю строку.
        self._disable_notifications = True
        theme = os.environ.get("TUI_THEME", "ansi-dark")
        if theme not in self.available_themes:
            theme = "ansi-dark"
        self.theme = theme

        self.system = MusicSystem()
        self.playlists = PlaylistStore()
        self.folders = FolderStore()
        self.player = PlayerController(self.system.find_track)

        try:
            self.startup_cache = self.system.load_from_disk()
        except Exception as exc:
            self.startup_cache = {
                "found": False,
                "loaded": 0,
                "edges": 0,
                "path": "",
                "error": str(exc),
            }

        self.table = DataTable(id="tracks")
        self.recommendations = DataTable(id="recommendations")
        self.queue_table = DataTable(id="queue_table")
        self.playlists_table = DataTable(id="playlists_table")
        self.side_tabs = TabbedContent(id="side-tabs", initial="tab-recs")
        self.filter_input = Input(
            placeholder="поиск...",
            id="filter",
            compact=True,
        )
        self.stats = Static(id="stats")
        self.player_bar = PlayerBar(id="player_bar")
        self._filter_text = ""
        self._queue_signature = None
        self._last_player_error = None
        self._last_playlists_signature = None
        self._poll_timer = None
        self._layout = None
        self._recommendation_ids: list[str] = []

        # Автоматическое слежение за папками. Снимок содержит только
        # realpath/mtime/size; аудиофайлы при этом не изменяются.
        self._watch_interval = watch_interval_seconds()
        self._watch_timer = None
        self._watch_snapshot = None
        self._watch_check_running = False
        self._watch_sync_running = False
        self._watch_sync_queued = False
        self._watch_pending_paths: set[str] = set()
        self._analysis_running = False
        self._analysis_state: Optional[dict[str, Any]] = None
        self._status_timer = None
        self._status_text = ""
        self._status_severity = "information"

    def get_default_screen(self) -> Screen:
        return MainScreen(id="_default")

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: str = "information",
        timeout: Optional[float] = None,
        markup: bool = True,
    ) -> None:
        """Показывает уведомление в нижней строке вместо Textual toast."""
        text = str(message)
        if title:
            text = f"{title}: {text}" if text else title
        text = " ".join(text.split())
        self.post_message(StatusNotice(text, severity, timeout))

    def on_status_notice(self, event: StatusNotice) -> None:
        self._show_status(event.message, event.severity, event.timeout)

    def _notification_lines(self):
        for screen in self.screen_stack:
            try:
                yield screen.query_one("#notification_line", NotificationLine)
            except NoMatches:
                continue

    def _apply_status_line(self, line: NotificationLine) -> None:
        line.update(Text(self._status_text))
        line.set_classes("")
        if self._status_severity == "error":
            line.add_class("-error")
        elif self._status_severity == "warning":
            line.add_class("-warning")

    def _sync_notification_line(self, line: NotificationLine) -> None:
        """Заполняет строку на только что открытом экране."""
        if self._status_text:
            self._apply_status_line(line)

    def _show_status(
        self,
        message: str,
        severity: str,
        timeout: Optional[float],
    ) -> None:
        text = " ".join(str(message).split())
        if not text:
            self._clear_status()
            return

        self._status_text = text
        self._status_severity = severity
        if self._status_timer is not None:
            self._status_timer.stop()
        delay = self.NOTIFICATION_TIMEOUT if timeout is None else timeout
        self._status_timer = self.set_timer(
            max(0.1, float(delay)),
            self._clear_status,
        )
        for line in self._notification_lines():
            self._apply_status_line(line)

    def _clear_status(self) -> None:
        self._status_timer = None
        self._status_text = ""
        self._status_severity = "information"
        for line in self._notification_lines():
            line.update("")
            line.set_classes("")

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True, icon="♫", time_format="%H:%M")
        yield self.stats
        with Horizontal(id="main_toolbar"):
            yield Button(
                "ПАПКИ",
                id="folders_button",
                variant="primary",
                compact=True,
            )
            yield Button("+ ТРЕК", id="add_track_button", compact=True)
            yield Button("− ТРЕК", id="delete_track_button", compact=True)
            yield Button("СВЯЗЬ", id="connect_button", compact=True)
            yield Button("АНАЛИЗ", id="info_button", compact=True)
            yield Button("ПЛЕЙЛИСТЫ", id="playlists_button", compact=True)
            yield Button("СПРАВКА", id="help_button", compact=True)
        with Horizontal(id="body"):
            with Vertical(id="library_panel"):
                with Horizontal(id="library_header"):
                    yield Static("БИБЛИОТЕКА", id="library_title")
                    yield self.filter_input
                    yield Button(
                        "×",
                        id="clear_filter_button",
                        compact=True,
                        tooltip="Сбросить фильтр",
                    )
                yield self.table
            with Vertical(id="side_panel"):
                with self.side_tabs:
                    with TabPane("РЕК.", id="tab-recs"):
                        yield self.recommendations
                        with Vertical(id="rec_actions"):
                            with Horizontal(classes="action-row"):
                                yield Button(
                                    "ИГРАТЬ",
                                    id="rec_play_button",
                                    compact=True,
                                )
                                yield Button(
                                    "В ОЧЕРЕДЬ",
                                    id="rec_enqueue_button",
                                    compact=True,
                                )
                            with Horizontal(classes="action-row"):
                                yield Button(
                                    "ПОДБОР",
                                    id="rec_refresh_button",
                                    compact=True,
                                )
                                yield Button(
                                    "ВСЕ",
                                    id="rec_all_button",
                                    compact=True,
                                )
                                yield Button(
                                    "РАДИО",
                                    id="rec_radio_button",
                                    compact=True,
                                    tooltip="Радио по выбранному треку (R)",
                                )
                        yield Static(
                            "E — все · r — подбор",
                            classes="pane-hint",
                        )
                    with TabPane("ОЧЕРЕДЬ", id="tab-queue"):
                        yield self.queue_table
                        with Vertical(id="queue_actions"):
                            with Horizontal(classes="action-row"):
                                yield Button(
                                    "ИГРАТЬ",
                                    id="queue_play_button",
                                    compact=True,
                                )
                                yield Button(
                                    "УБРАТЬ",
                                    id="queue_remove_button",
                                    compact=True,
                                )
                                yield Button(
                                    "ШАФЛ",
                                    id="queue_shuffle_button",
                                    compact=True,
                                    tooltip="Перемешать очередь (S)",
                                )
                            with Horizontal(classes="action-row"):
                                yield Button(
                                    "ВВЕРХ",
                                    id="queue_up_button",
                                    compact=True,
                                )
                                yield Button(
                                    "ВНИЗ",
                                    id="queue_down_button",
                                    compact=True,
                                )
                                yield Button(
                                    "ОЧИСТ.",
                                    id="queue_clear_button",
                                    compact=True,
                                )
                        yield Static(
                            "S — перемешать · ^K — очистить",
                            classes="pane-hint",
                        )
                    with TabPane("ПЛЕЙЛИСТЫ", id="tab-playlists"):
                        yield self.playlists_table
                        with Horizontal(id="playlist_actions"):
                            yield Button(
                                "МЕНЕДЖЕР",
                                id="playlist_manager_button",
                                compact=True,
                            )
                            yield Button(
                                "ИГРАТЬ",
                                id="playlist_play_button",
                                compact=True,
                            )
                        yield Static(
                            "Enter — играть",
                            classes="pane-hint",
                        )
        yield self.player_bar
        with Vertical(id="player_controls"):
            with Horizontal(classes="player-row"):
                yield Button(
                    "|◀",
                    id="prev_button",
                    compact=True,
                    tooltip="Предыдущий трек (b)",
                )
                yield Button(
                    "▶",
                    id="play_pause_button",
                    compact=True,
                    tooltip="Играть/пауза (Space)",
                )
                yield Button(
                    "▶|",
                    id="next_button",
                    compact=True,
                    tooltip="Следующий трек (n)",
                )
                yield Button(
                    "■",
                    id="stop_button",
                    compact=True,
                    tooltip="Стоп (x)",
                )
                yield Button(
                    "◀◀",
                    id="seek_back_button",
                    compact=True,
                    tooltip="Назад на 5 секунд (Ctrl+←)",
                )
                yield Button(
                    "▶▶",
                    id="seek_forward_button",
                    compact=True,
                    tooltip="Вперёд на 5 секунд (Ctrl+→)",
                )
                yield Button(
                    "▼",
                    id="volume_down_button",
                    compact=True,
                    tooltip="Тише (−)",
                )
                yield Button(
                    "▲",
                    id="volume_up_button",
                    compact=True,
                    tooltip="Громче (+)",
                )
            with Horizontal(classes="player-row"):
                yield Button(
                    SHUFFLE_ICON,
                    id="shuffle_button",
                    compact=True,
                    tooltip="Режим shuffle (s)",
                )
                yield Button(
                    REPEAT_ICON,
                    id="repeat_button",
                    compact=True,
                    tooltip="Режим повтора (l)",
                )
                yield Button(
                    "≡",
                    id="open_queue_button",
                    compact=True,
                    tooltip="Открыть очередь (u)",
                )
                yield Button(
                    "▶≡",
                    id="play_all_button",
                    compact=True,
                    tooltip="Играть всю библиотеку",
                )
                yield Button(
                    "⊕",
                    id="enqueue_button",
                    compact=True,
                    tooltip="Добавить выбранный трек в очередь (e)",
                )
                yield Button(
                    "◐",
                    id="theme_button",
                    compact=True,
                    tooltip="Переключить ANSI-тему (F2)",
                )
        yield Footer(compact=False, show_command_palette=False)

    def on_mount(self) -> None:
        self.title = "tui music"
        self.sub_title = "ANSI RICE PLAYER"

        self._apply_layout(self.size.width)
        self._refresh_playlists()
        self._sync_player()
        self._update_stats()
        self._update_toolbar_state()
        self._poll_timer = self.set_interval(0.4, self._poll_player)
        self._watch_timer = self.set_interval(
            self._watch_interval,
            self._watch_tick,
        )
        if self.folders.watch_enabled and len(self.folders):
            self.set_timer(0.8, self._watch_tick)
        self.table.focus()

        if self.startup_cache.get("error"):
            self.notify(
                f"Сохранение не загружено: {self.startup_cache['error']}",
                severity="error",
                timeout=10,
            )

        if self.playlists.error:
            self.notify(self.playlists.error, severity="warning", timeout=8)
        if self.folders.error:
            self.notify(self.folders.error, severity="warning", timeout=8)

    async def action_quit(self) -> None:
        # Закрываем mpv до выхода из TUI, не оставляя фоновую сессию.
        self.player.shutdown()
        await super().action_quit()

    def on_unmount(self) -> None:
        if self._status_timer is not None:
            self._status_timer.stop()
            self._status_timer = None
        self._watch_snapshot = None
        self._watch_pending_paths.clear()
        self.player.shutdown()

    def format_title(self, title: str, sub_title: str) -> Content:
        if not sub_title:
            return Content(title).stylize("bold")
        return Content.assemble(
            Content(title).stylize("bold"),
            "  ·  ",
            Content(sub_title).stylize("dim"),
        )

    def _setup_table(self, table: DataTable, columns) -> None:
        table.clear(columns=True)
        for label, width in columns:
            table.add_column(label, width=width)
        table.cursor_type = "row"
        table.zebra_stripes = False

    def _layout_columns(self):
        if self._layout == "wide":
            return {
                "tracks": (
                    ("ID", 4),
                    ("ТРЕК", 24),
                    ("АРТИСТ", 16),
                    ("АЛЬБОМ", 18),
                    ("ЖАНР", 9),
                    ("BPM", 4),
                    ("НАСТР.", 8),
                    ("ДЛИТ.", 5),
                    ("★", 3),
                ),
                "recommendations": (
                    ("ID", 4),
                    ("ТРЕК", 18),
                    ("АРТИСТ", 10),
                    ("BPM", 4),
                    ("НАСТР.", 7),
                    ("★", 3),
                ),
                "queue": (
                    ("#", 3),
                    ("", 2),
                    ("ТРЕК", 22),
                    ("АРТИСТ", 12),
                    ("ДЛИТ.", 5),
                ),
                "playlists": (("НАЗВАНИЕ", 22), ("ТРЕКОВ", 7)),
            }
        if self._layout == "compact":
            return {
                "tracks": (
                    ("ID", 4),
                    ("ТРЕК", 20),
                    ("АРТИСТ", 15),
                    ("BPM", 4),
                    ("ДЛИТ.", 5),
                    ("★", 3),
                ),
                "recommendations": (
                    ("ID", 4),
                    ("ТРЕК", 14),
                    ("BPM", 4),
                    ("★", 3),
                ),
                "queue": (
                    ("#", 2),
                    ("", 1),
                    ("ТРЕК", 18),
                    ("ДЛИТ.", 4),
                ),
                "playlists": (("НАЗВАНИЕ", 22), ("ТРЕКОВ", 7)),
            }
        return {
            "tracks": (
                ("ID", 4),
                ("ТРЕК", 18),
                ("АРТИСТ", 9),
                ("BPM", 4),
            ),
            "recommendations": (
                ("ID", 4),
                ("ТРЕК", 13),
            ),
            "queue": (
                ("#", 2),
                ("", 1),
                ("ТРЕК", 12),
            ),
            "playlists": (("НАЗВАНИЕ", 13), ("ТРЕКОВ", 4)),
        }

    def _apply_layout(self, width: int) -> None:
        mode = "wide" if width >= 180 else "compact" if width >= 110 else "narrow"
        if mode == self._layout:
            return
        selected = self._table_track_id(self.table)
        selected_rec = self._table_track_id(self.recommendations)
        self._layout = mode
        columns = self._layout_columns()
        self._setup_table(self.table, columns["tracks"])
        self._setup_table(self.recommendations, columns["recommendations"])
        self._setup_table(self.queue_table, columns["queue"])
        self._setup_table(self.playlists_table, columns["playlists"])
        self.load_tracks(preserve_id=selected)
        self._render_recommendations(preserve_id=selected_rec)
        self._queue_signature = None
        self._refresh_queue()
        self._last_playlists_signature = None
        self._refresh_playlists()

    def on_resize(self, event) -> None:
        self._apply_layout(event.size.width)

    # ------------------------------------------------------------------ library

    def _library_row(self, track) -> tuple:
        bpm = f"{track.bpm:.0f}" if track.bpm else "—"
        duration = fmt_time(track.duration)
        if self._layout == "wide":
            return (
                str(track.id),
                track.title,
                track.artist,
                track.album or "",
                track.genre or "",
                bpm,
                track.mood or "—",
                duration,
                str(track.rating),
            )
        if self._layout == "compact":
            return (
                str(track.id),
                track.title,
                track.artist,
                bpm,
                duration,
                str(track.rating),
            )
        return (
            str(track.id),
            track.title,
            track.artist,
            bpm,
        )

    def load_tracks(self, preserve_id: Optional[str] = None) -> None:
        current = preserve_id or self._table_track_id(self.table)
        query = self._filter_text.casefold().strip()
        self.table.clear()

        tracks = sorted(
            self.system.table.values(),
            key=lambda track: _id_sort_key(track.id),
        )
        for track in tracks:
            haystack = " ".join(
                str(value or "")
                for value in (
                    track.title,
                    track.artist,
                    track.album,
                    track.genre,
                    track.mood,
                )
            ).casefold()
            if query and query not in haystack:
                continue
            self.table.add_row(
                *self._library_row(track),
                key=str(track.id),
            )

        if self.table.row_count:
            target = 0
            if current:
                try:
                    target = self.table.get_row_index(current)
                except Exception:
                    target = 0
            self.table.move_cursor(row=min(target, self.table.row_count - 1))
        self._queue_signature = None
        self._update_toolbar_state()

    def _update_toolbar_state(self) -> None:
        """Кнопки действий с треком активны только при выбранной строке."""
        has_track = bool(self.table.row_count)
        for button_id in (
            "delete_track_button",
            "connect_button",
            "info_button",
            "play_all_button",
            "rec_radio_button",
        ):
            try:
                button = self.query_one(f"#{button_id}", Button)
            except Exception:
                continue
            button.disabled = not has_track

    def _table_track_id(self, table: DataTable) -> Optional[str]:
        if not table.row_count:
            return None
        try:
            row = table.get_row_at(table.cursor_row)
        except Exception:
            return None
        if not row:
            return None
        if table is self.queue_table:
            index = table.cursor_row
            if 0 <= index < len(self.player.queue):
                return self.player.queue[index]
            return None
        return str(row[0])

    def _table_playlist_name(self) -> Optional[str]:
        if not self.playlists_table.row_count:
            return None
        try:
            row = self.playlists_table.get_row_at(self.playlists_table.cursor_row)
        except Exception:
            return None
        return str(row[0]) if row else None

    def _active_track_id(self) -> Optional[str]:
        focused = self.focused
        if focused is self.recommendations:
            return self._table_track_id(self.recommendations)
        if focused is self.queue_table:
            return self._table_track_id(self.queue_table)
        return self._table_track_id(self.table)

    def action_focus_filter(self) -> None:
        self.filter_input.focus()

    def action_clear_filter(self) -> None:
        if self.focused is self.filter_input:
            self.filter_input.value = ""
            self.table.focus()
        elif self._filter_text:
            self.filter_input.value = ""

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "filter":
            self._filter_text = event.value
            self.load_tracks()

    def _analysis_progress_text(self, state: dict[str, Any]) -> Text:
        theme = self.current_theme
        accent = rich_color(theme.accent, "green")
        primary = rich_color(theme.primary, "blue")
        muted = f"{rich_color(theme.foreground, 'default')} dim"
        stage = str(state.get("stage") or "analyze")
        labels = {
            "scan": "СКАН",
            "analyze": "АНАЛИЗ",
            "graph": "ГРАФ",
            "save": "СОХР.",
            "error": "ОШИБКА",
        }
        label = labels.get(stage, stage.upper())
        processed = int(state.get("processed") or 0)
        total = int(state.get("total") or 0)
        text = Text()
        text.append("  ")
        text.append(label, style=f"bold {accent}")
        if total:
            percent = min(100.0, max(0.0, processed * 100.0 / total))
            text.append(
                f" {processed}/{total}",
                style=f"bold {primary}",
            )
            text.append(f" · {percent:.0f}%", style=muted)
        else:
            text.append(f" {processed}", style=f"bold {primary}")
        text.append(
            f" · {format_duration(state.get('elapsed_seconds'))}",
            style=muted,
        )
        if stage == "graph":
            phase = state.get("phase") or state.get("message")
            if phase:
                text.append(f" · {phase}", style=muted)
        else:
            current_name = state.get("current_name")
            if current_name and self._layout != "narrow":
                text.append(f" · {current_name}", style=muted)
        failed = int(state.get("failed") or 0)
        if failed:
            text.append(f" · ошибок {failed}", style=muted)
        return text

    def _set_analysis_state(self, state: dict[str, Any]) -> None:
        self._analysis_state = state
        if state.get("status") == "running":
            stage = str(state.get("stage") or "analyze")
            labels = {
                "scan": "СКАН",
                "analyze": "АНАЛИЗ",
                "graph": "ГРАФ",
                "save": "СОХР.",
            }
            label = labels.get(stage, stage.upper())
            total = int(state.get("total") or 0)
            processed = int(state.get("processed") or 0)
            if total:
                self.sub_title = f"{label} {processed}/{total}"
            else:
                self.sub_title = label
        else:
            self.sub_title = "ANSI RICE PLAYER"
        self._update_stats()

    def _analysis_progress(self, state: dict[str, Any]) -> None:
        """Принимает обновление из worker-потока анализа."""
        try:
            self.app.call_from_thread(self._set_analysis_state, state)
        except Exception:
            try:
                self._set_analysis_state(state)
            except Exception:
                pass

    def _update_stats(self) -> None:
        if (
            self._analysis_state
            and self._analysis_state.get("status") == "running"
        ):
            self.stats.update(
                self._analysis_progress_text(self._analysis_state)
            )
            return

        tracks = self.system.table.values()
        edges = len(self.system.graph.edges)
        analyzed = sum(1 for track in tracks if track.has_analysis)
        folders_count = len(self.folders)
        text = Text()
        theme = self.current_theme
        accent = rich_color(theme.accent, "green")
        primary = rich_color(theme.primary, "blue")
        muted = f"{rich_color(theme.foreground, 'default')} dim"
        if self._layout == "narrow":
            text.append("  ♪ ", style=accent)
            text.append(str(len(tracks)), style=f"bold {primary}")
            text.append("  ·  ", style=muted)
            text.append("◆ ", style=accent)
            text.append(str(edges), style=f"bold {primary}")
            text.append("  ·  ", style=muted)
            text.append(f"{folders_count} пап.", style=muted)
            text.append("  ·  ", style=muted)
            text.append(f"queue {len(self.player.queue)}", style=muted)
        else:
            text.append("  ♪ ", style=accent)
            text.append(f"{len(tracks)} треков", style=f"bold {primary}")
            text.append("  ·  ", style=muted)
            text.append(f"{edges} связей", style=f"bold {primary}")
            text.append("  ·  ", style=muted)
            text.append(f"{analyzed}/{len(tracks)} анализ", style=f"bold {primary}")
            text.append("  ·  ", style=muted)
            text.append(f"папок {folders_count}", style=muted)
            text.append("  ·  ", style=muted)
            text.append(f"queue {len(self.player.queue)}", style=muted)
        if self.player.shuffle:
            text.append("  ·  shuffle", style=accent)
        if self.player.repeat != "off":
            text.append(f"  ·  loop:{self.player.repeat}", style=accent)
        if self.folders.watch_enabled and len(self.folders):
            text.append(
                "  ·  W" if self._layout == "narrow" else "  ·  watch",
                style=accent,
            )
        self.stats.update(text)

    # ------------------------------------------------------------------- player

    def _sync_player(self) -> None:
        status = self.player.status()
        track = self.system.find_track(status.current_id) if status.current_id else None
        self.player_bar.update_status(status, track)
        self._update_play_button(status)
        self._update_shuffle_button(status)
        self._update_repeat_button(status)
        self._refresh_queue(status)
        self._update_stats()
        if status.error:
            self._notify_player_error(status.error)

    def _update_play_button(self, status: PlayerStatus) -> None:
        try:
            button = self.query_one("#play_pause_button", Button)
        except Exception:
            return
        if status.is_playing:
            button.label = "❚❚"
            button.tooltip = "Пауза (Space)"
        else:
            button.label = "▶"
            button.tooltip = "Играть (Space)"

    def _update_shuffle_button(self, status: PlayerStatus) -> None:
        """Показывает режим shuffle как нажатую кнопку.

        Состояние берётся из PlayerController, поэтому кнопка остаётся
        нажатой и после смены очереди/трека двойным кликом.
        """
        try:
            button = self.query_one("#shuffle_button", Button)
        except Exception:
            return
        button.label = (
            f"{SHUFFLE_ICON}•" if status.shuffle else SHUFFLE_ICON
        )
        button.variant = "primary" if status.shuffle else "default"
        button.tooltip = (
            "Режим shuffle: вкл (s)"
            if status.shuffle
            else "Режим shuffle: выкл (s)"
        )

    def _update_repeat_button(self, status: PlayerStatus) -> None:
        try:
            button = self.query_one("#repeat_button", Button)
        except Exception:
            return
        active = status.repeat != "off"
        button.label = f"{REPEAT_ICON}•" if active else REPEAT_ICON
        button.variant = "primary" if active else "default"
        repeat_name = {"all": "все", "one": "один"}.get(
            status.repeat, status.repeat
        )
        button.tooltip = (
            f"Режим повтора: {repeat_name} (l)"
            if active
            else "Режим повтора: выкл (l)"
        )

    def _poll_player(self) -> None:
        try:
            status = self.player.tick()
        except Exception as exc:
            self._notify_player_error(str(exc))
            return
        track = self.system.find_track(status.current_id) if status.current_id else None
        self.player_bar.update_status(status, track)
        self._update_play_button(status)
        self._update_shuffle_button(status)
        self._update_repeat_button(status)
        signature = (tuple(status.queue), status.queue_index)
        if signature != self._queue_signature:
            self._refresh_queue(status)
            self._update_stats()
        if status.error:
            self._notify_player_error(status.error)

    def _notify_player_error(self, message: str) -> None:
        if message and message != self._last_player_error:
            self._last_player_error = message
            self.notify(message, severity="error", timeout=8)

    def _run_player(self, action, *args):
        try:
            return action(*args)
        except (PlayerError, PlaybackError) as exc:
            self._notify_player_error(str(exc))
            self._sync_player()
            return None

    def _refresh_queue(self, status: Optional[PlayerStatus] = None) -> None:
        status = status or self.player.status()
        signature = (tuple(status.queue), status.queue_index)
        if signature == self._queue_signature:
            return
        self._queue_signature = signature
        self.queue_table.clear()
        for index, track_id in enumerate(status.queue):
            track = self.system.find_track(track_id)
            if track is None:
                artist, title, duration = "?", f"[missing {track_id}]", "—"
            else:
                artist, title = track.artist, track.title
                duration = fmt_time(track.duration)
            marker = "▶" if index == status.queue_index else ""
            if self._layout == "wide":
                cells = (
                    str(index + 1),
                    marker,
                    title,
                    artist,
                    duration,
                )
            elif self._layout == "compact":
                cells = (
                    str(index + 1),
                    marker,
                    title,
                    duration,
                )
            else:
                cells = (
                    str(index + 1),
                    marker,
                    title,
                )
            self.queue_table.add_row(*cells, key=f"q{index}")
        if self.queue_table.row_count:
            try:
                self.queue_table.move_cursor(
                    row=min(
                        max(status.queue_index, 0),
                        self.queue_table.row_count - 1,
                    )
                )
            except Exception:
                pass

    def action_play_pause(self) -> None:
        if self.player.current_id is None and not self.player.queue:
            track_id = self._table_track_id(self.table)
            if track_id:
                self._run_player(self.player.play_track, track_id)
                self._sync_player()
                return
        self._run_player(self.player.toggle)
        self._sync_player()

    def _play_library_track(self, track_id: str) -> None:
        """Играет трек из общей библиотеки.

        Если выбранного трека нет в очереди, очередь заменяется всей
        библиотекой. При включённом shuffle порядок очереди случайный,
        иначе — обычный порядок библиотеки.
        """
        track_id = str(track_id)
        if track_id in self.player.queue:
            self._run_player(self.player.play_track, track_id)
            self._sync_player()
            return

        shuffle_enabled = self.player.shuffle
        ids = self.system.library_play_order(
            randomize=shuffle_enabled,
        )
        if track_id not in ids:
            # Например, у добавленного вручную трека нет файла. Пусть
            # player.play_track покажет понятную ошибку.
            self._run_player(self.player.play_track, track_id)
            self._sync_player()
            return

        self._run_player(self.player.set_queue, ids, False)
        # Замена очереди не должна сбрасывать режим, выбранный кнопкой SHUF.
        self.player.shuffle = shuffle_enabled
        self._run_player(self.player.play_queue_index, ids.index(track_id))
        self._sync_player()
        order = "случайном" if self.player.shuffle else "обычном"
        self.notify(
            f"Очередь по всей библиотеке в {order} порядке: "
            f"{len(ids)} треков",
            timeout=6,
        )

    def action_play_selected(self) -> None:
        focused = self.focused
        if focused is self.playlists_table:
            name = self._table_playlist_name()
            if not name:
                self.notify("Плейлист не выбран", severity="error")
                return
            ids = self.playlists.get(name)
            if not ids:
                self.notify("Плейлист пуст", severity="error")
                return
            self._run_player(self.player.set_queue, ids, True)
            self._sync_player()
            self.notify(f"Играю плейлист «{name}»")
            return
        if focused is self.table:
            track_id = self._table_track_id(self.table)
            if not track_id:
                self.notify("Трек не выбран", severity="error")
                return
            self._play_library_track(track_id)
            return
        if focused is self.queue_table:
            if not self.queue_table.row_count:
                return
            self._run_player(
                self.player.play_queue_index,
                self.queue_table.cursor_row,
            )
            self._sync_player()
            return
        track_id = self._active_track_id()
        if not track_id:
            self.notify("Трек не выбран", severity="error")
            return
        self._run_player(self.player.play_track, track_id)
        self._sync_player()

    def action_stop(self) -> None:
        self._run_player(self.player.stop)
        self._sync_player()

    def action_next_track(self) -> None:
        if self._run_player(self.player.next) is False:
            self.notify("Конец очереди")
        self._sync_player()

    def action_previous_track(self) -> None:
        self._run_player(self.player.previous)
        self._sync_player()

    def action_seek_back(self) -> None:
        self._run_player(self.player.seek_relative, -5.0)
        self._sync_player()

    def action_seek_forward(self) -> None:
        self._run_player(self.player.seek_relative, 5.0)
        self._sync_player()

    def action_volume_up(self) -> None:
        self.player.change_volume(5)
        self.notify(f"Громкость: {self.player.volume}%")
        self._sync_player()

    def action_volume_down(self) -> None:
        self.player.change_volume(-5)
        self.notify(f"Громкость: {self.player.volume}%")
        self._sync_player()

    def action_toggle_shuffle(self) -> None:
        enabled = self.player.toggle_shuffle()
        self.notify(f"Shuffle: {'on' if enabled else 'off'}")
        self._sync_player()

    def action_shuffle_queue(self) -> None:
        count = self.player.shuffle_queue()
        if count < 2:
            self.notify(
                "В очереди меньше двух треков",
                severity="warning",
            )
            return
        self._sync_player()
        self.notify(f"Очередь перемешана: {count} треков")

    def action_cycle_repeat(self) -> None:
        mode = self.player.cycle_repeat()
        names = {"off": "off", "all": "all", "one": "one"}
        self.notify(f"Loop: {names.get(mode, mode)}")
        self._sync_player()

    def action_enqueue(self) -> None:
        track_id = self._active_track_id()
        if not track_id:
            self.notify("Трек не выбран", severity="error")
            return
        count = self.player.enqueue([track_id])
        self._sync_player()
        self.notify(f"В очередь добавлено: {count}")

    def action_enqueue_recommendations(self) -> None:
        if self.focused in (self.recommendations, self.queue_table):
            track_id = self._active_track_id()
        else:
            track_id = self._table_track_id(self.table)
        track_id = track_id or self._active_track_id()
        if not track_id:
            self.notify("Трек не выбран", severity="error")
            return
        recs = self.system.recommend(track_id)
        if not recs:
            self.notify("Рекомендаций нет", severity="warning")
            return
        self.player.enqueue([track.id for track in recs])
        self._sync_player()
        self.notify(f"В очередь добавлено рекомендаций: {len(recs)}")

    def _radio_seed_id(self, prefer_recommendation=False) -> Optional[str]:
        if prefer_recommendation and self.side_tabs.active == "tab-recs":
            track_id = self._table_track_id(self.recommendations)
            if track_id:
                return track_id
        return self._table_track_id(self.table) or self._active_track_id()

    def _start_radio(self, track_id: str) -> None:
        seed_track = self.system.find_track(track_id)
        ids = self.system.radio_track_ids(track_id, limit=30)
        if not ids:
            self.notify(
                "Нет похожих треков для радио",
                severity="warning",
            )
            return
        self._run_player(self.player.set_queue, ids, True)
        self._sync_player()
        self.side_tabs.active = "tab-queue"
        self.queue_table.focus()
        seed_label = (
            f"{seed_track.artist} — {seed_track.title}"
            if seed_track is not None
            else track_id
        )
        self.notify(
            f"Радио от «{seed_label}»: {len(ids)} треков",
            timeout=8,
        )

    def action_radio(self) -> None:
        track_id = self._active_track_id()
        if not track_id:
            self.notify("Трек не выбран", severity="error")
            return
        self._start_radio(track_id)

    def _radio_from_button(self) -> None:
        track_id = self._radio_seed_id(prefer_recommendation=True)
        if not track_id:
            self.notify("Трек не выбран", severity="error")
            return
        self._start_radio(track_id)

    def action_play_all(self) -> None:
        self.push_screen(PlayAllScreen(), self._play_all_choice)

    def _play_all_choice(self, choice: Optional[str]) -> None:
        if choice not in ("first", "random"):
            return
        ids = self.system.library_play_order(
            randomize=(choice == "random")
        )
        if not ids:
            self.notify("В библиотеке нет треков с файлами", severity="warning")
            return
        self._run_player(self.player.set_queue, ids, True)
        self._sync_player()
        start = "со случайного" if choice == "random" else "с первого"
        self.notify(
            f"Играю всю библиотеку {start}: {len(ids)} треков",
            timeout=8,
        )

    def action_remove_from_queue(self) -> None:
        if self.focused is not self.queue_table:
            self.notify("Фокус не на очереди", severity="warning")
            return
        if not self.queue_table.row_count:
            return
        self._run_player(self.player.remove_queue_index, self.queue_table.cursor_row)
        self._sync_player()

    def action_move_queue_up(self) -> None:
        if self.focused is not self.queue_table or not self.queue_table.row_count:
            return
        index = self.queue_table.cursor_row
        if index <= 0:
            return
        self._run_player(self.player.move_queue, index, index - 1)
        self._sync_player()
        self.queue_table.move_cursor(row=index - 1)

    def action_move_queue_down(self) -> None:
        if self.focused is not self.queue_table or not self.queue_table.row_count:
            return
        index = self.queue_table.cursor_row
        if index >= self.queue_table.row_count - 1:
            return
        self._run_player(self.player.move_queue, index, index + 1)
        self._sync_player()
        self.queue_table.move_cursor(row=index + 1)

    def action_clear_queue(self) -> None:
        self.player.clear_queue()
        self._sync_player()
        self.notify("Очередь очищена")

    def action_open_queue(self) -> None:
        self.side_tabs.active = "tab-queue"
        self.queue_table.focus()

    def action_open_playlists(self) -> None:
        self.side_tabs.active = "tab-playlists"
        self._refresh_playlists()
        self.playlists_table.focus()

    def action_manage_playlists(self) -> None:
        self.push_screen(
            PlaylistManagerScreen(self._active_track_id()),
            self._playlists_changed,
        )

    def _playlists_changed(self, _result=None) -> None:
        self._refresh_playlists()
        self._update_stats()

    def _refresh_playlists(self) -> None:
        signature = tuple(
            (name, len(self.playlists.get(name))) for name in self.playlists.names()
        )
        if signature == self._last_playlists_signature:
            return
        self._last_playlists_signature = signature
        current = self._table_playlist_name()
        self.playlists_table.clear()
        for name in self.playlists.names():
            self.playlists_table.add_row(
                name,
                str(self.playlists.count(name)),
                key=name,
            )
        if self.playlists_table.row_count:
            target = 0
            if current:
                try:
                    target = self.playlists_table.get_row_index(current)
                except Exception:
                    target = 0
            self.playlists_table.move_cursor(
                row=min(target, self.playlists_table.row_count - 1)
            )

    # ------------------------------------------------------------- library ops

    def _recommendation_row(self, track) -> tuple:
        bpm = f"{track.bpm:.0f}" if track.bpm else "—"
        if self._layout == "wide":
            return (
                str(track.id),
                track.title,
                track.artist,
                bpm,
                track.mood or "—",
                str(track.rating),
            )
        if self._layout == "compact":
            return (
                str(track.id),
                track.title,
                bpm,
                str(track.rating),
            )
        return (
            str(track.id),
            track.title,
        )

    def _render_recommendations(self, preserve_id: Optional[str] = None) -> None:
        current = preserve_id or self._table_track_id(self.recommendations)
        self.recommendations.clear()
        for track_id in self._recommendation_ids:
            track = self.system.find_track(track_id)
            if track is None:
                continue
            self.recommendations.add_row(
                *self._recommendation_row(track),
                key=str(track.id),
            )
        if self.recommendations.row_count and current:
            try:
                self.recommendations.move_cursor(
                    row=self.recommendations.get_row_index(current)
                )
            except Exception:
                pass

    def _set_recommendations(self, recs) -> None:
        self._recommendation_ids = [str(track.id) for track in recs]
        self._render_recommendations()

    def _show_recommendations_for(self, track_id: str) -> None:
        recs = self.system.recommend(track_id)
        self._set_recommendations(recs)
        if not recs:
            self.side_tabs.active = "tab-recs"
            self.notify("Нет рекомендаций: загрузите папку с музыкой")
            return
        self.side_tabs.active = "tab-recs"
        self.recommendations.focus()
        self.notify(f"Рекомендаций: {len(recs)}")

    def action_show_recommendations(self) -> None:
        if self.focused in (self.recommendations, self.queue_table):
            track_id = self._active_track_id()
        else:
            track_id = self._table_track_id(self.table)
        if not track_id:
            self.notify("Трек не выбран", severity="error")
            return
        self._show_recommendations_for(track_id)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table in (
            self.table,
            self.recommendations,
            self.queue_table,
            self.playlists_table,
        ):
            self.action_play_selected()

    def action_show_info(self) -> None:
        track_id = self._active_track_id()
        if not track_id:
            self.notify("Трек не выбран", severity="error")
            return
        track = self.system.find_track(track_id)
        if track is None:
            self.notify("Трек не найден", severity="error")
            return
        self.push_screen(AnalysisScreen(track))

    def action_delete_track(self) -> None:
        track_id = self._table_track_id(self.table)
        if not track_id:
            self.notify("Трек не выбран", severity="error")
            return
        if not self.system.delete_track(track_id):
            self.notify("Не удалось удалить трек", severity="error")
            return
        self.player.remove_track(track_id)
        self.load_tracks()
        self._set_recommendations([])
        self._sync_player()
        self._save_library()
        self.notify(f"Удалён трек {track_id}")

    def track_added(self, track: Optional[Track]) -> None:
        if track is None:
            return
        if not track.id or not track.title:
            self.notify("ID и название обязательны", severity="error")
            return
        if not self.system.add_track(track):
            self.notify("Трек с таким ID уже существует", severity="error")
            return
        self.load_tracks()
        self._sync_player()
        self._save_library()
        self._update_stats()
        self.notify("Трек добавлен")

    def action_add_track(self) -> None:
        self.push_screen(AddTrackScreen(), self.track_added)

    def relation_added(self, data: Optional[dict]) -> None:
        if not data:
            return
        result = self.system.add_relation(
            data["id1"], data["id2"], data["weight"]
        )
        self._save_library()
        self.notify(str(result))
        selected = self._table_track_id(self.table)
        if selected:
            self._show_recommendations_for(selected)

    def action_connect_tracks(self) -> None:
        self.push_screen(AddRelationScreen(), self.relation_added)

    def action_save_library(self) -> None:
        self._save_library(notify=True)

    def _save_library(self, notify: bool = False) -> None:
        try:
            result = self.system.save_to_disk()
        except Exception as exc:
            self.notify(f"Не удалось сохранить: {exc}", severity="error")
            return None
        if notify:
            self.notify(
                "Сохранено: "
                f"{result['tracks']} треков, {result['edges']} связей\n"
                f"{result['path']}",
                timeout=6,
            )
        return result

    def action_manage_folders(self) -> None:
        self.push_screen(FolderManagerScreen(), self._folders_result)

    def _folders_result(self, result: Optional[dict]) -> None:
        if not result:
            return
        action = result.get("action")
        path = result.get("path")
        if action == "add" and path:
            self.add_music_folder(path)
        elif action == "scan" and path:
            self.scan_music_folder(path)
        elif action == "remove" and path:
            self._remove_music_folder(path, True)
        elif action == "toggle_watch":
            self.set_watch_enabled(bool(result.get("enabled")))

    def _library_busy(self) -> bool:
        return self._analysis_running or self._watch_sync_running

    def set_watch_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self.folders.watch_enabled == enabled:
            return
        self.folders.set_watch_enabled(enabled)
        self._watch_snapshot = None
        self._watch_pending_paths.clear()
        self._watch_sync_queued = False
        if enabled:
            self.notify("Слежение за папками включено", timeout=5)
            if len(self.folders):
                self.set_timer(0.3, self._watch_tick)
        else:
            self.notify("Слежение за папками выключено", timeout=5)
        self._update_stats()

    def add_music_folder(self, path: str) -> None:
        if self._library_busy():
            self.notify(
                "Библиотека сейчас обновляется. Попробуйте через пару секунд.",
                severity="warning",
                timeout=6,
            )
            return
        try:
            path = self.folders.add(path)
        except ValueError as exc:
            self.notify(str(exc), severity="error")
            return
        self._analysis_running = True
        self._watch_snapshot = None
        self._update_stats()
        self.notify(
            f"Папка добавлена: {display_folder(path)}. "
            "Сканирую и строю общий граф...",
            timeout=8,
        )
        self._analyze_directory(path)

    def scan_music_folder(self, path: str) -> None:
        if self._library_busy():
            self.notify(
                "Библиотека сейчас обновляется. Попробуйте через пару секунд.",
                severity="warning",
                timeout=6,
            )
            return
        path = normalize_folder(path)
        if not os.path.isdir(path):
            self.notify("Папка не найдена", severity="error")
            return
        self._analysis_running = True
        self._watch_snapshot = None
        self._update_stats()
        self.notify(
            "Сканирую папку и обновляю общий граф связей...",
            timeout=8,
        )
        self._analyze_directory(path)

    def _remove_music_folder(self, path: str, confirmed: bool) -> None:
        if not confirmed:
            return
        if self._library_busy():
            self.notify(
                "Библиотека сейчас обновляется. Попробуйте через пару секунд.",
                severity="warning",
                timeout=6,
            )
            return
        if not self.folders.remove(path):
            self.notify("Папка уже отсутствует в списке", severity="warning")
            return
        remaining = self.folders.names()
        removed_ids = []
        for track in list(self.system.table.values()):
            if not track.filepath or not path_is_within(track.filepath, path):
                continue
            if any(path_is_within(track.filepath, item) for item in remaining):
                continue
            if self.system.delete_track(track.id):
                removed_ids.append(str(track.id))
        for track_id in removed_ids:
            self.player.remove_track(track_id)
            self.playlists.remove_track_everywhere(track_id)
        self._watch_snapshot = None
        self._watch_pending_paths.clear()
        self._watch_sync_queued = False
        self.load_tracks()
        self._set_recommendations([])
        self._sync_player()
        self._refresh_playlists()
        self._update_stats()
        self._save_library()
        self.notify(
            f"Папка убрана: {display_folder(path)} "
            f"(треков: {len(removed_ids)})",
            timeout=8,
        )

    # ------------------------------------------------------- folder watching

    def _watch_tick(self) -> None:
        if not self.folders.watch_enabled or not len(self.folders):
            return
        if (
            self._watch_check_running
            or self._analysis_running
            or self._watch_sync_running
        ):
            return
        self._watch_check_running = True
        self._watch_check_worker()

    @work(thread=True, group="folder-watch")
    def _watch_check_worker(self) -> None:
        try:
            snapshot = collect_snapshot(self.folders.names())
        except Exception:
            try:
                self.app.call_from_thread(self._watch_check_failed)
            except Exception:
                pass
            return
        try:
            self.app.call_from_thread(self._watch_checked, snapshot)
        except Exception:
            pass

    def _watch_check_failed(self) -> None:
        self._watch_check_running = False

    def _watch_checked(self, snapshot) -> None:
        self._watch_check_running = False
        if not self.folders.watch_enabled:
            return

        expected = library_snapshot(
            self.system.table.values(),
            self.folders.names(),
        )
        previous = (
            expected if self._watch_snapshot is None else self._watch_snapshot
        )
        change = diff_snapshots(previous, snapshot.files, snapshot.roots)

        # Если корень временно недоступен (например, отключён внешний диск)
        # или его не удалось полностью прочитать, сохраняем прошлый снимок.
        # Пропавшие из-за этого файлы нельзя считать удалёнными.
        protected_roots = set(snapshot.root_errors)
        protected_roots.update(
            root
            for root, exists in snapshot.roots.items()
            if not exists
        )
        if protected_roots:
            change = FolderChange(
                added=change.added,
                removed=frozenset(
                    path
                    for path in change.removed
                    if not any(
                        path_is_within(path, root)
                        for root in protected_roots
                    )
                ),
                modified=change.modified,
            )

        next_snapshot = dict(snapshot.files)
        if protected_roots:
            for path, signature in previous.items():
                if path in next_snapshot:
                    continue
                if any(
                    path_is_within(path, root) for root in protected_roots
                ):
                    if signature is not None:
                        next_snapshot[path] = signature
        self._watch_snapshot = next_snapshot

        if not change.has_changes:
            self._watch_pending_paths.clear()
            return
        self._queue_watch_sync(change)

    def _queue_watch_sync(self, change: FolderChange) -> None:
        if not self.folders.watch_enabled:
            return
        self._watch_pending_paths.update(change.removed)
        self._watch_pending_paths.update(change.modified)
        if self._analysis_running or self._watch_sync_running:
            self._watch_sync_queued = True
            return
        self._watch_sync_queued = False
        pending = set(self._watch_pending_paths)
        self._watch_pending_paths.clear()
        self._watch_sync_running = True
        self._watch_sync_worker(pending)

    @work(thread=True, exclusive=True, group="folder-sync")
    def _watch_sync_worker(self, pending_paths: set[str]) -> None:
        removed_ids: list[str] = []
        try:
            roots = [
                path
                for path in self.folders.names()
                if os.path.isdir(path)
            ]
            if not roots:
                result = "Папки недоступны; библиотека не изменена."
            else:
                pending = {
                    os.path.realpath(path)
                    for path in pending_paths
                    if any(path_is_within(path, root) for root in roots)
                }
                for track in list(self.system.table.values()):
                    if not track.filepath:
                        continue
                    try:
                        real_path = os.path.realpath(track.filepath)
                    except OSError:
                        continue
                    track_root = None
                    for root in roots:
                        if path_is_within(real_path, root):
                            track_root = root
                            break
                    if track_root is None:
                        continue

                    # Для старых сохранений без подписи файла заполняем её
                    # без повторного анализа. Это чистое чтение stat().
                    if (
                        track.file_mtime_ns is None
                        or track.file_size is None
                    ) and os.path.isfile(real_path):
                        try:
                            file_stat = os.stat(real_path)
                        except OSError:
                            pass
                        else:
                            track.file_mtime_ns = file_stat.st_mtime_ns
                            track.file_size = file_stat.st_size

                    # Удаляем запись только по явному событию от слежения и
                    # только пока корень папки существует. Пропавший диск не
                    # должен очищать библиотеку.
                    if real_path in pending and os.path.isdir(track_root):
                        if self.system.delete_track(track.id):
                            removed_ids.append(str(track.id))

                scan_roots = [
                    root for root in roots if os.path.isdir(root)
                ]
                if not scan_roots:
                    result = (
                        "Папки временно недоступны; библиотека не изменена."
                    )
                else:
                    result = self.system.load_from_folders(
                        scan_roots,
                        save=False,
                        progress=self._analysis_progress,
                        incremental=True,
                    )
                save_stats = self.system.save_to_disk()
                result += (
                    "\nБиблиотека сохранена: "
                    f"{save_stats['tracks']} треков, "
                    f"{save_stats['edges']} связей"
                )
        except Exception as exc:
            result = f"Ошибка автообновления библиотеки: {exc}"

        try:
            self.app.call_from_thread(
                self._watch_sync_finished,
                result,
                removed_ids,
            )
        except Exception:
            pass

    def _watch_sync_finished(
        self,
        result: str,
        removed_ids: list[str],
    ) -> None:
        self._watch_sync_running = False
        self._analysis_state = None
        self.sub_title = "ANSI RICE PLAYER"
        for track_id in removed_ids:
            self.player.remove_track(track_id)
            self.playlists.remove_track_everywhere(track_id)

        # Снимок, который запустил синхронизацию, уже содержит все файлы,
        # включая дубликаты и не-аудио. Оставляем его базой, иначе такие
        # файлы будут бесконечно считаться «новыми».
        self.load_tracks()
        self._set_recommendations([])
        self._sync_player()
        self._refresh_playlists()
        self._update_stats()
        if result:
            self.notify(str(result), timeout=12)
        if self._watch_sync_queued:
            self._watch_sync_queued = False
            self.set_timer(0.2, self._watch_tick)

    def _play_queue_at_cursor(self) -> None:
        if not self.queue_table.row_count:
            self.notify("Очередь пуста", severity="warning")
            return
        self._run_player(
            self.player.play_queue_index,
            self.queue_table.cursor_row,
        )
        self._sync_player()

    def _remove_queue_at_cursor(self) -> None:
        if not self.queue_table.row_count:
            return
        self._run_player(
            self.player.remove_queue_index,
            self.queue_table.cursor_row,
        )
        self._sync_player()

    def _move_queue_at_cursor(self, delta: int) -> None:
        if not self.queue_table.row_count:
            return
        index = self.queue_table.cursor_row
        target = index + delta
        if not (0 <= target < self.queue_table.row_count):
            return
        self._run_player(self.player.move_queue, index, target)
        self._sync_player()
        self.queue_table.move_cursor(row=target)

    def _play_recommendation(self) -> None:
        track_id = self._table_track_id(self.recommendations)
        if not track_id:
            self.notify("Нет рекомендаций", severity="warning")
            return
        self._run_player(self.player.play_track, track_id)
        self._sync_player()

    def _enqueue_recommendation(self) -> None:
        track_id = self._table_track_id(self.recommendations)
        if not track_id:
            self.notify("Нет рекомендаций", severity="warning")
            return
        count = self.player.enqueue([track_id])
        self._sync_player()
        self.notify(f"В очередь добавлено: {count}")

    def _enqueue_all_recommendations(self) -> None:
        track_id = self._table_track_id(self.recommendations)
        if not track_id:
            self.notify("Нет рекомендаций", severity="warning")
            return
        recs = self.system.recommend(track_id)
        if not recs:
            self.notify("Рекомендаций нет", severity="warning")
            return
        self.player.enqueue([track.id for track in recs])
        self._sync_player()
        self.notify(f"В очередь добавлено рекомендаций: {len(recs)}")

    def _play_playlist_at_cursor(self) -> None:
        name = self._table_playlist_name()
        if not name:
            self.notify("Плейлист не выбран", severity="warning")
            return
        ids = self.playlists.get(name)
        if not ids:
            self.notify("Плейлист пуст", severity="warning")
            return
        self._run_player(self.player.set_queue, ids, True)
        self._sync_player()
        self.notify(f"Играю плейлист «{name}»")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        actions = {
            "folders_button": self.action_manage_folders,
            "add_track_button": self.action_add_track,
            "delete_track_button": self.action_delete_track,
            "connect_button": self.action_connect_tracks,
            "info_button": self.action_show_info,
            "playlists_button": self.action_manage_playlists,
            "help_button": self.action_show_readme,
            "clear_filter_button": self.action_clear_filter,
            "rec_play_button": self._play_recommendation,
            "rec_refresh_button": self.action_show_recommendations,
            "rec_enqueue_button": self._enqueue_recommendation,
            "rec_all_button": self._enqueue_all_recommendations,
            "rec_radio_button": self._radio_from_button,
            "queue_play_button": self._play_queue_at_cursor,
            "queue_remove_button": self._remove_queue_at_cursor,
            "queue_shuffle_button": self.action_shuffle_queue,
            "queue_up_button": lambda: self._move_queue_at_cursor(-1),
            "queue_down_button": lambda: self._move_queue_at_cursor(1),
            "queue_clear_button": self.action_clear_queue,
            "playlist_manager_button": self.action_manage_playlists,
            "playlist_play_button": self._play_playlist_at_cursor,
            "prev_button": self.action_previous_track,
            "play_pause_button": self.action_play_pause,
            "next_button": self.action_next_track,
            "stop_button": self.action_stop,
            "seek_back_button": self.action_seek_back,
            "seek_forward_button": self.action_seek_forward,
            "volume_down_button": self.action_volume_down,
            "volume_up_button": self.action_volume_up,
            "shuffle_button": self.action_toggle_shuffle,
            "repeat_button": self.action_cycle_repeat,
            "open_queue_button": self.action_open_queue,
            "play_all_button": self.action_play_all,
            "enqueue_button": self.action_enqueue,
            "theme_button": self.action_toggle_theme,
        }
        action = actions.get(event.button.id)
        if action:
            action()

    @work(thread=True, exclusive=True, group="analysis")
    def _analyze_directory(self, path: str) -> None:
        try:
            result = self.system.load_from_directory(
                path,
                progress=self._analysis_progress,
            )
        except Exception as exc:
            result = f"Ошибка анализа: {exc}"
        self.app.call_from_thread(self._analysis_finished, result)

    def _analysis_finished(self, result: str) -> None:
        self._analysis_running = False
        self._analysis_state = None
        self.sub_title = "ANSI RICE PLAYER"
        self._watch_snapshot = None
        self.load_tracks()
        self._set_recommendations([])
        self._sync_player()
        self._refresh_playlists()
        self._update_stats()
        self.notify(str(result), timeout=12)
        if self.folders.watch_enabled and (
            self._watch_sync_queued or self._watch_pending_paths
        ):
            self._watch_sync_queued = False
            self._queue_watch_sync(FolderChange())

    def action_show_readme(self) -> None:
        self.push_screen(ReadMeScreen())

    def action_toggle_theme(self) -> None:
        self.theme = "ansi-light" if self.theme == "ansi-dark" else "ansi-dark"
        self.notify(f"Тема: {self.theme}")
        self._sync_player()


def print_analysis_status() -> int:
    state = read_analysis_state()
    print(format_analysis_state(state))
    return 0 if state else 1


def install_exit_handlers(app: MusicApp) -> None:
    """Гарантирует остановку mpv при завершении приложения."""

    def handle_signal(signum, _frame) -> None:
        try:
            app.player.shutdown()
        finally:
            signal.signal(signum, signal.SIG_DFL)
            os.kill(os.getpid(), signum)

    for name in ("SIGTERM", "SIGHUP"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            signal.signal(signum, handle_signal)
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    if "--status" in sys.argv[1:]:
        raise SystemExit(print_analysis_status())
    app = MusicApp()
    atexit.register(app.player.shutdown)
    install_exit_handlers(app)
    try:
        app.run()
    finally:
        app.player.shutdown()
