"""Живое состояние анализа библиотеки.

Состояние пишется в ``analysis_state.json`` атомарно после каждого
заметного шага. Это позволяет видеть прогресс снаружи процесса, даже если
TUI ещё не обновил библиотеку. Сам трекер ничего не сохраняет в библиотеку
и не изменяет аудиофайлы.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from app_paths import analysis_state_path


STATE_FORMAT = "tui-music-analysis-state"
STATE_VERSION = 1

STAGE_LABELS = {
    "idle": "ожидание",
    "scan": "сканирование",
    "analyze": "анализ",
    "graph": "граф",
    "save": "сохранение",
    "done": "готово",
    "error": "ошибка",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def format_duration(seconds: Any) -> str:
    try:
        total = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        total = 0
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with open(temp, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.write("\n")
        os.replace(temp, path)
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass


def read_analysis_state(path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    target = Path(path).expanduser() if path else analysis_state_path()
    try:
        with open(target, "r", encoding="utf-8") as file:
            state = json.load(file)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(state, dict):
        return None
    if state.get("format") != STATE_FORMAT:
        return None
    return state


def format_analysis_state(state: Optional[dict[str, Any]]) -> str:
    """Короткое человекочитаемое описание состояния для CLI."""
    if not state:
        return "Состояние анализа не найдено."

    status = str(state.get("status") or "unknown")
    stage = str(state.get("stage") or "idle")
    stage_label = STAGE_LABELS.get(stage, stage)
    processed = int(state.get("processed") or 0)
    total = int(state.get("total") or 0)
    elapsed = format_duration(state.get("elapsed_seconds"))
    lines = [f"Этап: {stage_label}"]
    if status != "done":
        if total:
            percent = min(100.0, max(0.0, processed * 100.0 / total))
            lines.append(
                f"Прогресс: {processed}/{total} ({percent:.0f}%)"
            )
        else:
            lines.append(f"Прогресс: {processed}")
    lines.append(f"Время: {elapsed}")
    current_name = state.get("current_name") or state.get("current")
    if current_name:
        lines.append(f"Сейчас: {current_name}")
    message = state.get("message")
    if message:
        lines.append(f"Сообщение: {message}")
    if status == "done":
        tracks = state.get("tracks")
        edges = state.get("edges")
        if tracks is not None:
            lines.append(f"Треков: {tracks}")
        if edges is not None:
            lines.append(f"Связей: {edges}")
    if status == "error":
        error = state.get("error") or message or "неизвестная ошибка"
        lines.append(f"Ошибка: {error}")
    if state.get("pid"):
        lines.append(f"PID: {state['pid']}")
    if state.get("updated_at"):
        lines.append(f"Обновлено: {state['updated_at']}")
    return "\n".join(lines)


class AnalysisTracker:
    """Пишет живое состояние анализа в JSON и уведомляет подписчика."""

    def __init__(
        self,
        path: Optional[Path] = None,
        on_update: Optional[Callable[[dict[str, Any]], None]] = None,
        min_interval: float = 0.15,
    ):
        self.path = (
            Path(path).expanduser() if path else analysis_state_path()
        )
        self.on_update = on_update
        self.min_interval = max(0.0, float(min_interval))
        self._lock = threading.RLock()
        self._state: dict[str, Any] = {}
        self._started_monotonic = time.monotonic()
        self._last_write = 0.0
        self._last_emit = 0.0

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def start(
        self,
        stage: str = "scan",
        total: int = 0,
        message: str = "",
        **extra: Any,
    ) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            self._started_monotonic = now
            self._last_write = 0.0
            self._last_emit = 0.0
            self._state = {
                "format": STATE_FORMAT,
                "version": STATE_VERSION,
                "pid": os.getpid(),
                "status": "running",
                "stage": stage,
                "processed": 0,
                "total": max(0, int(total or 0)),
                "message": message,
                "current": "",
                "current_name": "",
                "started_at": _utc_now(),
                "updated_at": _utc_now(),
                "elapsed_seconds": 0.0,
                **extra,
            }
            self._write(force=True)
            self._emit(force=True)
            return dict(self._state)

    def update(
        self,
        stage: Optional[str] = None,
        processed: Optional[int] = None,
        total: Optional[int] = None,
        current: Optional[str] = None,
        current_name: Optional[str] = None,
        message: Optional[str] = None,
        force: bool = False,
        **extra: Any,
    ) -> dict[str, Any]:
        with self._lock:
            if not self._state:
                self.start(stage or "scan", total or 0, message or "")
            state = self._state
            if stage is not None:
                state["stage"] = stage
            if processed is not None:
                state["processed"] = max(0, int(processed))
            if total is not None:
                state["total"] = max(0, int(total))
            if current is not None:
                state["current"] = str(current)
                if current_name is None:
                    state["current_name"] = Path(str(current)).name
            if current_name is not None:
                state["current_name"] = str(current_name)
            if message is not None:
                state["message"] = str(message)
            state.update(extra)
            state["status"] = "running"
            state["updated_at"] = _utc_now()
            state["elapsed_seconds"] = round(
                max(0.0, time.monotonic() - self._started_monotonic),
                1,
            )
            self._write(force=force)
            self._emit(force=force)
            return dict(state)

    def finish(self, message: str = "готово", **extra: Any) -> dict[str, Any]:
        with self._lock:
            state = self._state
            state.update(extra)
            state["status"] = "done"
            state["stage"] = "done"
            state["message"] = message
            state["current"] = ""
            state["current_name"] = ""
            state["updated_at"] = _utc_now()
            state["elapsed_seconds"] = round(
                max(0.0, time.monotonic() - self._started_monotonic),
                1,
            )
            state["finished_at"] = state["updated_at"]
            self._write(force=True)
            self._emit(force=True)
            return dict(state)

    def fail(self, error: str, **extra: Any) -> dict[str, Any]:
        with self._lock:
            state = self._state
            state.update(extra)
            state["status"] = "error"
            state["stage"] = "error"
            state["message"] = error
            state["error"] = str(error)
            state["updated_at"] = _utc_now()
            state["elapsed_seconds"] = round(
                max(0.0, time.monotonic() - self._started_monotonic),
                1,
            )
            state["finished_at"] = state["updated_at"]
            self._write(force=True)
            self._emit(force=True)
            return dict(state)

    def _write(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_write < self.min_interval:
            return
        payload = dict(self._state)
        try:
            _atomic_write(self.path, payload)
        except OSError:
            # Трекер не должен ломать сам анализ из-за проблем с диском.
            return
        self._last_write = now

    def _emit(self, force: bool = False) -> None:
        if self.on_update is None:
            return
        now = time.monotonic()
        if not force and now - self._last_emit < self.min_interval:
            return
        self._last_emit = now
        try:
            self.on_update(dict(self._state))
        except Exception:
            # Ошибка UI-подписчика не должна прерывать анализ.
            pass
