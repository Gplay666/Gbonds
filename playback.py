"""Плеерная подсистема с независимым от TUI backend-интерфейсом.

Сейчас используется mpv через JSON IPC. Управление очередью, shuffle,
repeat и громкостью живёт в PlayerController и не зависит от mpv, поэтому
другой backend (MPD, VLC, libmpv, GStreamer) можно добавить отдельным
классом, не переписывая интерфейс.
"""

from __future__ import annotations

import atexit
import json
import os
import random
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence


class PlaybackError(RuntimeError):
    """Ошибка воспроизведения или backend'а."""


class PlayerError(RuntimeError):
    """Ошибка действия плеера (нет файла, пустая очередь и т.п.)."""


@dataclass(frozen=True)
class BackendState:
    state: str = "stopped"  # playing | paused | stopped | error
    position: float = 0.0
    duration: float = 0.0
    volume: int = 80
    error: Optional[str] = None


@dataclass(frozen=True)
class PlayerStatus:
    current_id: Optional[str]
    state: str
    position: float
    duration: float
    volume: int
    shuffle: bool
    repeat: str
    queue: tuple[str, ...]
    queue_index: int
    error: Optional[str]

    @property
    def is_playing(self) -> bool:
        return self.state == "playing"

    @property
    def is_paused(self) -> bool:
        return self.state == "paused"


class PlaybackBackend:
    """Минимальный интерфейс аудио-backend'а.

    Backend умеет играть один файл. Очередь, repeat и shuffle — задача
    PlayerController, поэтому backend можно заменить на MPD или libVLC.
    """

    name = "base"

    def start(self) -> None:
        raise NotImplementedError

    def shutdown(self) -> None:
        raise NotImplementedError

    def play(self, filepath: str) -> None:
        raise NotImplementedError

    def pause(self) -> None:
        raise NotImplementedError

    def resume(self) -> None:
        raise NotImplementedError

    def stop(self) -> None:
        raise NotImplementedError

    def seek(self, position: float) -> None:
        raise NotImplementedError

    def set_volume(self, volume: int) -> None:
        raise NotImplementedError

    def snapshot(self) -> BackendState:
        raise NotImplementedError


class NullBackend(PlaybackBackend):
    """Заглушка, если mpv не установлен или backend отключён."""

    name = "disabled"

    def __init__(self, reason: str = "Аудио-backend не подключён."):
        self.reason = reason
        self._state = "stopped"

    def start(self) -> None:
        pass

    def shutdown(self) -> None:
        pass

    def play(self, filepath: str) -> None:
        raise PlaybackError(self.reason)

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def stop(self) -> None:
        self._state = "stopped"

    def seek(self, position: float) -> None:
        pass

    def set_volume(self, volume: int) -> None:
        pass

    def snapshot(self) -> BackendState:
        return BackendState(state=self._state, error=self.reason)


class MpvBackend(PlaybackBackend):
    """mpv через JSON IPC.

    mpv выбран как лёгкий, кроссплатформенный и управляемый вариант:
    у него есть pause/seek/volume/stop и он не требует отдельного демона.
    """

    name = "mpv"

    def __init__(self, volume: int = 80, audio_output: Optional[str] = None):
        self._volume = max(0, min(100, int(volume)))
        self._audio_output = audio_output or os.environ.get("TUI_MPV_AO")
        self._process: Optional[subprocess.Popen] = None
        self._socket: Optional[socket.socket] = None
        self._socket_path: Optional[Path] = None
        self._buffer = b""
        self._request_id = 0
        self._started = False
        atexit.register(self.shutdown)

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> None:
        if self._started and self._process and self._process.poll() is None:
            return
        if not shutil.which("mpv"):
            raise PlaybackError(
                "mpv не найден. Установите mpv или выберите другой backend."
            )

        self.shutdown()
        runtime_dir = Path(
            os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
        )
        socket_path = runtime_dir / f"tui-music-{os.getpid()}.sock"
        if len(str(socket_path)) > 100:
            socket_path = Path(tempfile.gettempdir()) / (
                f"tmr-{os.getpid()}-{int(time.time())}.sock"
            )
        try:
            socket_path.unlink()
        except FileNotFoundError:
            pass

        command = [
            "mpv",
            "--no-video",
            "--no-terminal",
            "--really-quiet",
            "--idle=yes",
            "--keep-open=no",
            "--audio-display=no",
            "--force-window=no",
            f"--volume={self._volume}",
            f"--input-ipc-server={socket_path}",
        ]
        if self._audio_output:
            command.append(f"--ao={self._audio_output}")
        self._process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        self._socket_path = socket_path

        deadline = time.monotonic() + 3.0
        last_error: Optional[Exception] = None
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise PlaybackError(
                    "mpv завершился при запуске. Проверьте установку аудио."
                )
            if socket_path.exists():
                try:
                    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    sock.settimeout(0.8)
                    sock.connect(str(socket_path))
                    self._socket = sock
                    self._buffer = b""
                    self._started = True
                    self.set_volume(self._volume)
                    return
                except OSError as exc:
                    last_error = exc
                    try:
                        sock.close()
                    except Exception:
                        pass
            time.sleep(0.03)

        self.shutdown()
        raise PlaybackError(f"Не удалось подключиться к mpv: {last_error}")

    def _readline(self) -> bytes:
        if self._socket is None:
            raise PlaybackError("Нет соединения с mpv.")
        while b"\n" not in self._buffer:
            try:
                chunk = self._socket.recv(65536)
            except socket.timeout as exc:
                raise PlaybackError("mpv IPC не отвечает.") from exc
            if not chunk:
                raise PlaybackError("mpv закрыл IPC-соединение.")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return line

    def _request(self, *command: object):
        if not self._started or self._socket is None:
            raise PlaybackError("mpv не запущен.")
        self._request_id += 1
        request_id = self._request_id
        payload = json.dumps(
            {"command": list(command), "request_id": request_id},
            ensure_ascii=False,
        ).encode("utf-8") + b"\n"
        try:
            self._socket.sendall(payload)
        except OSError as exc:
            raise PlaybackError(f"Ошибка записи в mpv: {exc}") from exc

        while True:
            line = self._readline()
            try:
                message = json.loads(line.decode("utf-8", "replace"))
            except json.JSONDecodeError:
                continue
            if message.get("request_id") != request_id:
                continue
            if message.get("error") != "success":
                raise PlaybackError(str(message.get("error") or "mpv error"))
            return message.get("data")

    def _property(self, name: str, default=None):
        try:
            value = self._request("get_property", name)
        except PlaybackError:
            return default
        return default if value is None else value

    def play(self, filepath: str) -> None:
        path = os.path.abspath(os.path.expanduser(filepath))
        if not os.path.isfile(path):
            raise PlaybackError(f"Файл не найден: {path}")
        self.start()
        self._request("loadfile", path, "replace")
        self._request("set_property", "pause", False)
        self._request("set_property", "mute", False)
        self.set_volume(self._volume)

    def pause(self) -> None:
        self._request("set_property", "pause", True)

    def resume(self) -> None:
        self._request("set_property", "pause", False)

    def stop(self) -> None:
        if self._started:
            try:
                self._request("stop")
            except PlaybackError:
                pass

    def seek(self, position: float) -> None:
        self._request("seek", max(0.0, float(position)), "absolute")

    def set_volume(self, volume: int) -> None:
        self._volume = max(0, min(100, int(volume)))
        if self._started:
            try:
                self._request("set_property", "volume", self._volume)
            except PlaybackError:
                pass

    def snapshot(self) -> BackendState:
        if not self._started:
            return BackendState(state="stopped", volume=self._volume)
        if self._process is None or self._process.poll() is not None:
            self._started = False
            return BackendState(
                state="error",
                volume=self._volume,
                error="mpv завершился.",
            )
        try:
            idle = bool(self._property("idle-active", True))
            paused = bool(self._property("pause", False))
            position = float(self._property("time-pos", 0) or 0)
            duration = float(self._property("duration", 0) or 0)
            volume = int(round(float(self._property("volume", self._volume) or self._volume)))
        except (TypeError, ValueError):
            position, duration, volume = 0.0, 0.0, self._volume
            idle, paused = True, False
        state = "stopped" if idle else ("paused" if paused else "playing")
        return BackendState(
            state=state,
            position=max(0.0, position),
            duration=max(0.0, duration),
            volume=max(0, min(100, volume)),
        )

    def shutdown(self) -> None:
        if self._socket is not None:
            try:
                self._request("quit")
            except Exception:
                pass
            try:
                self._socket.close()
            except Exception:
                pass
        self._socket = None
        self._started = False
        if self._process is not None:
            try:
                self._process.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                try:
                    self._process.terminate()
                    self._process.wait(timeout=1.0)
                except Exception:
                    try:
                        self._process.kill()
                    except Exception:
                        pass
                    try:
                        self._process.wait(timeout=1.0)
                    except Exception:
                        pass
        self._process = None
        if self._socket_path is not None:
            try:
                self._socket_path.unlink()
            except OSError:
                pass
        self._socket_path = None


def create_backend(preferred: Optional[str] = None, volume: int = 80) -> PlaybackBackend:
    """Создаёт backend по имени; сюда же добавлять MPD/VLC в будущем."""
    name = (preferred or os.environ.get("TUI_PLAYER_BACKEND") or "mpv").lower()
    if name in {"none", "null", "dummy", "off"}:
        return NullBackend("Аудио-backend отключён (TUI_PLAYER_BACKEND).")
    if name == "mpv":
        if shutil.which("mpv"):
            return MpvBackend(
                volume=volume,
                audio_output=os.environ.get("TUI_MPV_AO"),
            )
        return NullBackend("mpv не найден. Установите mpv для воспроизведения.")
    return NullBackend(f"Неизвестный аудио-backend: {name}")


class PlayerController:
    """Очередь, transport, shuffle/repeat и текущее состояние плеера."""

    def __init__(
        self,
        resolve_track: Callable[[str], object],
        backend: Optional[PlaybackBackend] = None,
        volume: int = 80,
    ):
        self._resolve_track = resolve_track
        self.backend = backend or create_backend(volume=volume)
        self.queue: list[str] = []
        self.queue_index = -1
        self.current_id: Optional[str] = None
        self.shuffle = False
        self.repeat = "off"  # off | all | one
        self.volume = max(0, min(100, int(volume)))
        self._state = "stopped"
        self._position = 0.0
        self._duration = 0.0
        self._started = False
        self._saw_playing = False
        self._idle_since: Optional[float] = None
        self.last_error: Optional[str] = None

    @property
    def current_track(self):
        return self._resolve_track(self.current_id) if self.current_id else None

    def status(self) -> PlayerStatus:
        return PlayerStatus(
            current_id=self.current_id,
            state=self._state,
            position=self._position,
            duration=self._duration,
            volume=self.volume,
            shuffle=self.shuffle,
            repeat=self.repeat,
            queue=tuple(self.queue),
            queue_index=self.queue_index,
            error=self.last_error,
        )

    def _ensure_started(self) -> None:
        if self._started:
            return
        self.backend.start()
        self._started = True
        self.backend.set_volume(self.volume)

    def _track_path(self, track_id: str) -> str:
        track = self._resolve_track(track_id)
        if track is None:
            raise PlayerError(f"Трек {track_id} не найден.")
        filepath = getattr(track, "filepath", None)
        if not filepath:
            raise PlayerError(
                "У трека нет пути к файлу. Загрузите папку с музыкой."
            )
        path = os.path.abspath(os.path.expanduser(str(filepath)))
        if not os.path.isfile(path):
            raise PlayerError(f"Файл не найден: {path}")
        return path

    def _play_current(self) -> None:
        if self.current_id is None:
            raise PlayerError("Нет текущего трека.")
        path = self._track_path(self.current_id)
        self._ensure_started()
        self.backend.play(path)
        track = self._resolve_track(self.current_id)
        self._state = "playing"
        self._position = 0.0
        self._duration = float(getattr(track, "duration", 0) or 0)
        self._saw_playing = False
        self._idle_since = None
        self.last_error = None

    def play_track(self, track_id: str) -> None:
        track_id = str(track_id)
        if track_id not in self.queue:
            self.queue.append(track_id)
        self.queue_index = self.queue.index(track_id)
        self.current_id = track_id
        self._play_current()

    def play_queue_index(self, index: int) -> None:
        if not 0 <= index < len(self.queue):
            raise PlayerError("Нет такого элемента очереди.")
        self.queue_index = index
        self.current_id = self.queue[index]
        self._play_current()

    def set_queue(self, track_ids: Sequence[str], autoplay: bool = False) -> None:
        ids = [str(track_id) for track_id in track_ids]
        self.stop()
        self.queue = ids
        self.queue_index = 0 if (autoplay and ids) else -1
        self.current_id = ids[0] if (autoplay and ids) else None
        if autoplay and ids:
            self._play_current()

    def enqueue(self, track_ids: Sequence[str], play_next: bool = False) -> int:
        ids = [str(track_id) for track_id in track_ids]
        if not ids:
            return 0
        if play_next and self.current_id in self.queue:
            insert_at = self.queue_index + 1
            self.queue[insert_at:insert_at] = ids
        else:
            self.queue.extend(ids)
        return len(ids)

    def enqueue_track(self, track_id: str, play_next: bool = False) -> None:
        self.enqueue([track_id], play_next=play_next)

    def remove_queue_index(self, index: int) -> None:
        if not 0 <= index < len(self.queue):
            raise PlayerError("Нет такого элемента очереди.")
        was_current = index == self.queue_index
        if was_current:
            try:
                if self._started:
                    self.backend.stop()
            except PlaybackError:
                pass
            self._state = "stopped"
            self._saw_playing = False
        self.queue.pop(index)
        if not self.queue:
            self.queue_index = -1
            self.current_id = None
            self._position = 0
            self._duration = 0
            return
        if was_current:
            self.queue_index = min(index, len(self.queue) - 1)
            self.current_id = self.queue[self.queue_index]
            try:
                self._play_current()
            except PlayerError as exc:
                self.last_error = str(exc)
        elif index < self.queue_index:
            self.queue_index -= 1

    def move_queue(self, from_index: int, to_index: int) -> None:
        if not (0 <= from_index < len(self.queue)):
            raise PlayerError("Нет такого элемента очереди.")
        to_index = max(0, min(to_index, len(self.queue) - 1))
        if from_index == to_index:
            return
        current = self.current_id
        item = self.queue.pop(from_index)
        self.queue.insert(to_index, item)
        if current is not None:
            self.queue_index = self.queue.index(current)
        elif self.queue:
            self.queue_index = min(self.queue_index, len(self.queue) - 1)

    def shuffle_queue(self) -> int:
        """Перемешивает очередь, сохраняя текущий трек и его воспроизведение."""
        if len(self.queue) < 2:
            return len(self.queue)
        current = self.current_id
        random.shuffle(self.queue)
        if current is not None and current in self.queue:
            self.queue_index = self.queue.index(current)
            self.current_id = current
        else:
            self.queue_index = -1
            self.current_id = None
        return len(self.queue)

    def remove_track(self, track_id: str) -> None:
        """Убирает трек из очереди (например, при удалении из библиотеки)."""
        track_id = str(track_id)
        if self.current_id == track_id:
            self.stop()
        self.queue = [item for item in self.queue if item != track_id]
        if self.current_id in self.queue:
            self.queue_index = self.queue.index(self.current_id)
        else:
            self.current_id = None
            self.queue_index = -1
            self._state = "stopped"
            self._position = 0
            self._duration = 0

    def clear_queue(self) -> None:
        self.stop()
        self.queue.clear()
        self.queue_index = -1
        self.current_id = None
        self._position = 0
        self._duration = 0

    def toggle(self) -> None:
        if self.current_id is None:
            if self.queue:
                self.play_queue_index(max(0, self.queue_index))
                return
            raise PlayerError("Очередь пуста.")
        if not self._started:
            self._play_current()
            return
        state = self.backend.snapshot()
        if state.state == "playing":
            self.backend.pause()
            self._state = "paused"
        elif state.state == "paused":
            self.backend.resume()
            self._state = "playing"
        elif state.state == "error":
            raise PlayerError(state.error or "Ошибка аудио-backend'а.")
        else:
            self._play_current()

    def stop(self) -> None:
        if self._started:
            try:
                self.backend.stop()
            except PlaybackError:
                pass
        self._state = "stopped"
        self._position = 0.0
        self._saw_playing = False
        self._idle_since = None

    def _next_index(self, automatic: bool) -> Optional[int]:
        if not self.queue:
            return None
        if self.shuffle and len(self.queue) > 1:
            choices = [i for i in range(len(self.queue)) if i != self.queue_index]
            return random.choice(choices) if choices else None
        index = self.queue_index + 1
        if index < len(self.queue):
            return index
        if self.repeat == "all":
            return 0
        return None

    def next(self) -> bool:
        index = self._next_index(automatic=False)
        if index is None:
            self.stop()
            return False
        self.queue_index = index
        self.current_id = self.queue[index]
        self._play_current()
        return True

    def previous(self) -> bool:
        if self.current_id and self._position > 3.0:
            self.seek_to(0.0)
            return True
        if not self.queue:
            return False
        index = self.queue_index - 1
        if index < 0:
            index = len(self.queue) - 1 if self.repeat == "all" else 0
        self.queue_index = index
        self.current_id = self.queue[index]
        self._play_current()
        return True

    def _after_end(self) -> None:
        self._saw_playing = False
        self._idle_since = None
        if self.repeat == "one" and self.current_id:
            try:
                self._play_current()
            except PlayerError as exc:
                self.last_error = str(exc)
            return
        index = self._next_index(automatic=True)
        if index is None:
            self._state = "stopped"
            self._position = 0.0
            return
        self.queue_index = index
        self.current_id = self.queue[index]
        try:
            self._play_current()
        except PlayerError as exc:
            self.last_error = str(exc)
            self._state = "stopped"

    def tick(self) -> PlayerStatus:
        if not self._started or self.current_id is None:
            return self.status()
        try:
            state = self.backend.snapshot()
        except PlaybackError as exc:
            self.last_error = str(exc)
            self._state = "error"
            return self.status()

        if self.volume != state.volume and state.volume:
            self.volume = state.volume
        self._position = max(0.0, state.position)
        if state.duration > 0:
            self._duration = state.duration
        self._state = state.state
        if state.error:
            self.last_error = state.error

        if state.state == "playing":
            self._saw_playing = True
            self._idle_since = None
        elif state.state == "stopped" and self._saw_playing:
            if self._idle_since is None:
                self._idle_since = time.monotonic()
            elif time.monotonic() - self._idle_since >= 0.7:
                self._after_end()
        elif state.state == "paused":
            self._idle_since = None
        return self.status()

    def seek_to(self, position: float) -> None:
        if self.current_id is None:
            raise PlayerError("Нет текущего трека.")
        target = max(0.0, float(position))
        if self._duration > 0:
            target = min(target, max(0.0, self._duration - 0.25))
        if self._started:
            self.backend.seek(target)
        self._position = target

    def seek_relative(self, delta: float) -> None:
        self.seek_to(self._position + float(delta))

    def set_volume(self, volume: int) -> None:
        self.volume = max(0, min(100, int(volume)))
        if self._started:
            self.backend.set_volume(self.volume)

    def change_volume(self, delta: int) -> None:
        self.set_volume(self.volume + int(delta))

    def toggle_shuffle(self) -> bool:
        self.shuffle = not self.shuffle
        return self.shuffle

    def cycle_repeat(self) -> str:
        modes = ("off", "all", "one")
        self.repeat = modes[(modes.index(self.repeat) + 1) % len(modes)]
        return self.repeat

    def shutdown(self) -> None:
        # Backend мог запуститься и оставить процесс mpv даже если
        # контроллер уже успел сбросить _started. Остановка идемпотентна.
        try:
            self.backend.shutdown()
        except Exception:
            pass
        self._started = False
