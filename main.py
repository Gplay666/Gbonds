import os
import random

from folder_store import (
    FolderStore,
    display_folder,
    normalize_folder,
    path_is_within,
    tracks_in_folder,
)
from grafLib import Graph
from hash_table import HashTable
from sorting import quicksort
from avl_tree import AVLTree
from collections import deque
from audio_loader import scan_music_directory
from track import Track
from analysis_tracker import AnalysisTracker
from audio_analysis import (
    analyze_library,
    build_similarity_graph,
    feature_similarity_scores,
    format_analysis,
    update_similarity_graph,
)
from library_store import default_analysis_log_path, default_library_path
from playlist_store import PlaylistStore


def bfs_recommendation_scores(graph, start, max_depth=2, limit=200):
    """Пары ``(track_id, score)`` для рекомендаций по графу."""
    if start not in graph.nodes:
        return []

    scores = {}
    queue = deque([(start, 0, 1.0)])

    while queue:
        node, depth, score = queue.popleft()
        if depth >= max_depth:
            continue

        for neighbor in graph.get_neighbors(node):
            if neighbor == start:
                continue
            weight = graph.get_edge_weight(node, neighbor, 0.0)
            if weight <= 0:
                continue

            new_score = score * weight * 0.65
            if new_score <= scores.get(neighbor, 0.0) + 1e-9:
                continue

            scores[neighbor] = new_score
            queue.append((neighbor, depth + 1, new_score))

    return sorted(
        scores.items(),
        key=lambda item: item[1],
        reverse=True,
    )[:limit]


def bfs_recommendations(graph, start, max_depth=2, limit=20):
    """Рекомендации по графу: лучший путь до вершины с затуханием веса."""
    return [
        node
        for node, _ in bfs_recommendation_scores(
            graph,
            start,
            max_depth=max_depth,
            limit=limit,
        )
    ]


class MusicSystem:
    def __init__(self):
        self.table = HashTable(100)
        self.tree = AVLTree()
        self.graph = Graph(directed=False)
    def add_track(self, track):
        if self.table.find(track.id):
            return False
        self.table.insert(track.id, track)
        self.tree.insert((track.rating, track.id))
        self.graph.add_node(track.id)
        return True
    def find_track(self, track_id):
        return self.table.find(track_id)

    def save_to_disk(self, path=None):
        """Сохраняет библиотеку и граф связей в JSON-файл."""
        from library_store import save_library

        return save_library(self, path)

    def load_from_disk(self, path=None):
        """Загружает библиотеку и граф из JSON-файла."""
        from library_store import load_library

        return load_library(self, path)

    def delete_track(self, track_id):
        track = self.table.find(track_id)
        if not track:
            return False
        self.table.delete(track_id)
        self.tree.delete((track.rating, track.id))
        self.graph.remove_node(track_id)
        return True
    def load_from_folders(
        self,
        directories,
        save=True,
        progress=None,
        incremental=False,
    ):
        """Сканирует папки, анализирует треки и обновляет общий граф.

        ``incremental=True`` пересчитывает только рёбра новых треков. Это
        используется автоматическим слежением: полная пересборка графа на
        большой библиотеке занимает десятки секунд и блокирует интерфейс.

        ``progress`` получает снимки состояния анализа. Сам трекер всегда
        пишет ``analysis_state.json``, поэтому прогресс виден и снаружи TUI.
        """
        tracker = AnalysisTracker(on_update=progress)
        tracker.start("scan", 0, "поиск аудиофайлов")

        scan_stats = {
            "loaded": 0,
            "skipped": 0,
            "added_ids": [],
            "errors": [],
        }
        analysis_stats = {
            "total": 0,
            "analyzed": 0,
            "skipped": 0,
            "failed": 0,
            "errors": [],
        }
        graph_stats = {
            "edges": 0,
            "average_weight": 0.0,
        }
        analysis_error = None
        save_error = None
        analyzed_failed = 0

        def scan_progress(index, total, path):
            tracker.update(
                "scan",
                index,
                total,
                current=path,
                message="сканирование файлов",
            )

        def analysis_progress(index, total, track, ok, error):
            nonlocal analyzed_failed
            if not ok:
                analyzed_failed += 1
            tracker.update(
                "analyze",
                index,
                total,
                current=track.filepath,
                current_name=f"{track.artist} — {track.title}",
                message="анализ аудио",
                failed=analyzed_failed,
                force=bool(total and index >= total),
            )

        def graph_progress(phase, processed, total, edges):
            tracker.update(
                "graph",
                processed,
                total,
                message=phase,
                phase=phase,
                edges=edges,
                force=bool(total and processed >= total),
            )

        try:
            for directory in directories:
                stats = scan_music_directory(
                    str(directory),
                    self,
                    progress=scan_progress,
                )
                scan_stats["loaded"] += stats["loaded"]
                scan_stats["skipped"] += stats["skipped"]
                scan_stats["added_ids"].extend(
                    stats.get("added_ids", [])
                )
                scan_stats["errors"].extend(stats["errors"])

            tracker.update(
                "scan",
                0,
                0,
                message=f"найдено треков: {scan_stats['loaded']}",
                loaded=scan_stats["loaded"],
                skipped=scan_stats["skipped"],
                force=True,
            )

            try:
                total_tracks = sum(
                    1 for track in self.table.values() if track.filepath
                )
                tracker.update(
                    "analyze",
                    0,
                    total_tracks,
                    message="анализ аудио",
                    force=True,
                )
                analysis_stats = analyze_library(
                    self,
                    progress=analysis_progress,
                )
                tracker.update(
                    "graph",
                    0,
                    1,
                    message="подготовка графа",
                    phase="подготовка",
                    force=True,
                )
                if incremental:
                    graph_stats = update_similarity_graph(
                        self,
                        scan_stats["added_ids"],
                        progress=graph_progress,
                    )
                else:
                    graph_stats = build_similarity_graph(
                        self,
                        progress=graph_progress,
                    )
            except Exception as exc:
                analysis_error = str(exc)

            lines = [
                f"Загружено треков: {scan_stats['loaded']}",
            ]
            if scan_stats["skipped"]:
                lines.append(
                    f"Пропущено (дубликаты/не аудио): {scan_stats['skipped']}"
                )
            if analysis_stats["analyzed"]:
                lines.append(
                    f"Проанализировано: {analysis_stats['analyzed']}"
                )
            if analysis_stats["failed"]:
                lines.append(
                    "Не удалось проанализировать: "
                    f"{analysis_stats['failed']}"
                )
            lines.append(f"Связей в графе: {graph_stats['edges']}")
            if graph_stats["edges"]:
                lines.append(
                    f"Средний вес связи: {graph_stats['average_weight']:.3f}"
                )
            if analysis_error:
                lines.append(f"Анализ недоступен: {analysis_error}")
            if scan_stats["errors"]:
                lines.append(
                    "Ошибки: " + "; ".join(scan_stats["errors"][:3])
                )
            if analysis_stats["errors"]:
                lines.append(
                    "Ошибки анализа: "
                    + "; ".join(analysis_stats["errors"][:3])
                )

            if save:
                tracker.update(
                    "save",
                    0,
                    1,
                    message="сохранение библиотеки",
                    force=True,
                )
                try:
                    save_stats = self.save_to_disk()
                    lines.append(
                        "Библиотека сохранена: "
                        f"{save_stats['tracks']} треков, "
                        f"{save_stats['edges']} связей"
                    )
                    tracker.update(
                        "save",
                        1,
                        1,
                        message="библиотека сохранена",
                        force=True,
                    )
                except Exception as exc:
                    save_error = str(exc)
                    lines.append(
                        f"Не удалось сохранить библиотеку: {exc}"
                    )

            log_path = default_analysis_log_path()
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as file:
                file.write("\n".join(lines) + "\n")

            final_stats = {
                "tracks": len(self.table.values()),
                "edges": len(self.graph.edges),
                "loaded": scan_stats["loaded"],
                "analyzed": analysis_stats["analyzed"],
                "failed": analysis_stats["failed"],
            }
            if analysis_error or save_error:
                tracker.fail(
                    analysis_error or save_error or "неизвестная ошибка",
                    **final_stats,
                )
            else:
                tracker.finish("анализ завершён", **final_stats)

            return "\n".join(lines)
        except Exception as exc:
            tracker.fail(str(exc))
            raise

    def load_from_directory(self, directory: str, save=True, progress=None):
        return self.load_from_folders(
            [directory],
            save=save,
            progress=progress,
        )

    def load_from_file(self, filename):
        try:
            loaded = 0
            with open(filename, "r", encoding="utf-8") as file:
                for line_number, line in enumerate(file, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split("|")
                    if len(parts) != 6:
                        print(
                            f"Ошибка в строке {line_number}: неверный формат."
                        )
                        continue
                    try:
                        track_id = parts[0]
                        title = parts[1]
                        artist = parts[2]
                        genre = parts[3]
                        duration = int(parts[4])
                        rating = int(parts[5])
                        track = Track(
                            track_id,
                            title,
                            artist,
                            genre,
                            duration,
                            rating
                        )
                        if self.add_track(track):
                            loaded += 1
                    except ValueError:
                        print(
                            f"Ошибка в строке {line_number}: "
                            f"duration/rating должны быть числами."
                        )
            return loaded
        except FileNotFoundError:
            print("Файл не найден.")
            return 0

    def load_relations(self, filename):
        try:
            loaded = 0
            with open(filename, "r", encoding="utf-8") as file:
                for line_number, line in enumerate(file, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    parts = line.split("|")
                    # минимум 2 поля: id1, id2
                    if len(parts) < 2:
                        print(f"Ошибка в строке {line_number}: неверный формат")
                        continue
                    a = parts[0]
                    b = parts[1]
                    weight = 1.0
                    if len(parts) >= 3 and parts[2] != "":
                        try:
                            weight = float(parts[2])
                        except ValueError:
                            print(f"Ошибка в строке {line_number}: вес должен быть числом")
                            continue
                    # проверка существования треков
                    if self.table.find(a) is None or self.table.find(b) is None:
                        print(f"Связь {a}-{b} пропущена: трек не найден.")
                        continue
                    self.connect_tracks(a, b, weight)
                    loaded += 1
            return f"\nЗагружено связей: {loaded}"
        except FileNotFoundError:
            return "Файл не найден."
    def add_relation(self, id1, id2, weight=1):
        if not self.table.find(id1):
            return "Первый трек не найден."
        if not self.table.find(id2):
            return "Второй трек не найден."
        self.graph.add_edge(id1, id2, weight)
        return "Связь добавлена."
    def show_sorted(self):
        pairs = self.tree.inorder()
        print("\n=== Треки по рейтингу ===")
        for rating, track_id in reversed(pairs):
            track = self.table.find(track_id)
            print(track)

    def recommend(self, track_id, limit=20):
        ids = bfs_recommendations(self.graph, track_id, limit=limit)
        tracks = []
        for tid in ids:
            track = self.table.find(tid)
            if track:
                tracks.append(track)
        return tracks

    def library_play_order(self, randomize=False, seed=None):
        """ID всех играбельных треков: по порядку библиотеки или случайно."""
        tracks = [
            track
            for track in self.table.values()
            if track.filepath
        ]
        tracks.sort(key=lambda track: self._id_sort_key(track.id))
        ids = [str(track.id) for track in tracks]
        if randomize:
            random.Random(seed).shuffle(ids)
        return ids

    def radio_track_ids(self, track_id, limit=30, seed=None):
        """Радио: похожие треки в случайном, но не хаотичном порядке.

        Похожесть считается напрямую по аудио-признакам и метаданным.
        Если анализ недоступен, используется граф. Итоговый порядок
        получается взвешенной случайной выборкой: более похожие треки
        выпадают чаще, но очередь не повторяет рейтинг один в один.
        """
        seed_track = self.find_track(track_id)
        if seed_track is None or not seed_track.filepath:
            return []
        seed_id = str(seed_track.id)
        limit = max(1, min(int(limit), 200))
        if limit == 1:
            return [seed_id]

        scores = feature_similarity_scores(self, seed_id)
        if not scores:
            scores = dict(
                bfs_recommendation_scores(
                    self.graph,
                    seed_id,
                    max_depth=2,
                    limit=max(limit * 4, 80),
                )
            )

        candidates = []
        for candidate_id, score in scores.items():
            candidate_id = str(candidate_id)
            track = self.find_track(candidate_id)
            if (
                track is None
                or not track.filepath
                or candidate_id == seed_id
            ):
                continue
            candidates.append((candidate_id, max(0.0, float(score))))

        rng = random.Random(seed)
        if not candidates:
            extras = [
                str(track.id)
                for track in self.table.values()
                if track.filepath and str(track.id) != seed_id
            ]
            rng.shuffle(extras)
            return [seed_id] + extras[:limit - 1]

        candidates.sort(key=lambda item: item[1], reverse=True)
        top_score = candidates[0][1]
        if top_score > 0:
            cutoff = top_score * 0.15
            filtered = [
                item for item in candidates if item[1] >= cutoff
            ]
            if filtered:
                candidates = filtered

        pool_size = max(limit * 4, 80)
        pool = candidates[:pool_size]
        chosen = []
        while pool and len(chosen) < limit - 1:
            current_top = max(score for _, score in pool)
            weights = []
            for _, score in pool:
                normalized = (
                    max(0.0, score) / current_top
                    if current_top > 0
                    else 1.0
                )
                # Степень < 1 смягчает порядок, jitter добавляет разброс.
                weight = normalized ** 0.6 + 0.04
                weights.append(weight * rng.uniform(0.75, 1.25))
            index = rng.choices(
                range(len(pool)),
                weights=weights,
                k=1,
            )[0]
            chosen.append(pool.pop(index)[0])

        if len(chosen) < limit - 1:
            used = {seed_id, *chosen}
            extras = [
                str(track.id)
                for track in self.table.values()
                if track.filepath and str(track.id) not in used
            ]
            rng.shuffle(extras)
            chosen.extend(extras[: limit - 1 - len(chosen)])

        return [seed_id] + chosen

    def reanalyze(self, force=True, progress=None):
        tracker = AnalysisTracker(on_update=progress)
        total = sum(
            1 for track in self.table.values() if track.filepath
        )
        failed = 0

        def analysis_progress(index, count, track, ok, error):
            nonlocal failed
            if not ok:
                failed += 1
            tracker.update(
                "analyze",
                index,
                count,
                current=track.filepath,
                current_name=f"{track.artist} — {track.title}",
                message="пересчёт анализа",
                failed=failed,
                force=bool(count and index >= count),
            )

        def graph_progress(phase, processed, count, edges):
            tracker.update(
                "graph",
                processed,
                count,
                message=phase,
                phase=phase,
                edges=edges,
                force=bool(count and processed >= count),
            )

        tracker.start("analyze", total, "пересчёт анализа")
        try:
            stats = analyze_library(
                self,
                force=force,
                progress=analysis_progress,
            )
            tracker.update(
                "graph",
                0,
                1,
                message="подготовка графа",
                phase="подготовка",
                force=True,
            )
            graph_stats = build_similarity_graph(
                self,
                progress=graph_progress,
            )
        except Exception as exc:
            tracker.fail(str(exc))
            return f"Ошибка анализа: {exc}"

        lines = [
            f"Проанализировано: {stats['analyzed']}",
            f"Пропущено: {stats['skipped']}",
            f"Ошибок: {stats['failed']}",
            f"Связей в графе: {graph_stats['edges']}",
        ]
        save_error = None
        try:
            save_stats = self.save_to_disk()
            lines.append(
                "Библиотека сохранена: "
                f"{save_stats['tracks']} треков, "
                f"{save_stats['edges']} связей"
            )
        except Exception as exc:
            save_error = str(exc)
            lines.append(f"Не удалось сохранить библиотеку: {exc}")
        if stats["errors"]:
            lines.append("Ошибки: " + "; ".join(stats["errors"][:3]))
        if save_error:
            tracker.fail(save_error)
        else:
            tracker.finish(
                "анализ завершён",
                tracks=len(self.table.values()),
                edges=len(self.graph.edges),
            )
        return "\n".join(lines)

    def show_analysis(self, track_id):
        track = self.table.find(track_id)
        if not track:
            return "Трек не найден."
        return format_analysis(track)

    def generate_report(self):
        tracks = self.table.values()
        sorted_tracks = quicksort(
            tracks,
            key=lambda t: t.rating
        )
        print("\n=== Отчёт ===")
        self.print_tracks(list(reversed(sorted_tracks)))
    def connect_tracks(self, a, b, weight=1):
        if a not in self.graph.nodes:
            self.graph.add_node(a)
        if b not in self.graph.nodes:
            self.graph.add_node(b)
        self.graph.add_edge(a, b, weight)
    def print_tracks(self, tracks):
        if not tracks:
            print("Список пуст.")
            return
        print("\n" + "=" * 120)
        print(
            f"{'ID':<6}"
            f"{'Исполнитель':<20}"
            f"{'Название':<28}"
            f"{'Альбом':<24}"
            f"{'Жанр':<14}"
            f"{'BPM':<7}"
            f"{'Настроение':<14}"
            f"{'Рейт.':<8}"
        )
        print("=" * 120)
        for track in tracks:
            print(
                f"{str(track.id):<6}"
                f"{track.artist[:18]:<20}"
                f"{track.title[:26]:<28}"
                f"{(track.album or '')[:22]:<24}"
                f"{(track.genre or '')[:12]:<14}"
                f"{(f'{track.bpm:.0f}' if track.bpm else '-'):<7}"
                f"{(track.mood or '-'):<14}"
                f"{track.rating:<8}"
            )
        print("=" * 120)
    def show_all_tracks(self):
        tracks = self.table.values()
        if not tracks:
            return "Треков нет"
        sorted_tracks = sorted(tracks, key=lambda t: self._id_sort_key(t.id))
        self.print_tracks(sorted_tracks)

    @staticmethod
    def _id_sort_key(track_id):
        try:
            return (0, int(track_id))
        except (TypeError, ValueError):
            return (1, str(track_id))

    def search_by_title(self, query):
        result = [
            track for track in self.table.values()
            if query.casefold() in track.title.casefold()
        ]
        self.print_tracks(result)
        return result
    def clear(self):
        self.table = HashTable(100)
        self.tree = AVLTree()
        self.graph = Graph(directed=False)

def _save_quietly(system):
    try:
        system.save_to_disk()
    except Exception as exc:
        print(f"Не удалось сохранить библиотеку: {exc}")


def _load_saved(system, quiet=False):
    result = system.load_from_disk()
    if result["error"]:
        if not quiet:
            print(f"Сохранение не загружено: {result['error']}")
        return result
    if result["found"] and not quiet:
        print(
            f"Загружено сохранение: {result['loaded']} треков, "
            f"{result['edges']} связей"
        )
    return result


def _add_folder_console(system, folders):
    directory = input("Папка с музыкой: ").strip()
    if not directory:
        return
    try:
        path = folders.add(directory)
    except ValueError as exc:
        path = normalize_folder(directory)
        if not os.path.isdir(path) or path not in folders:
            print(exc)
            return
        print("Папка уже в списке — пересканирую.")
    print(
        system.load_from_directory(
            path,
            progress=_console_analysis_progress,
        )
    )


def _console_analysis_progress(state):
    stage = str(state.get("stage") or "")
    labels = {
        "scan": "СКАН",
        "analyze": "АНАЛИЗ",
        "graph": "ГРАФ",
        "save": "СОХР.",
        "done": "ГОТОВО",
        "error": "ОШИБКА",
    }
    label = labels.get(stage, stage.upper() or "АНАЛИЗ")
    processed = int(state.get("processed") or 0)
    total = int(state.get("total") or 0)
    if total and state.get("status") not in ("done", "error"):
        percent = min(100.0, processed * 100.0 / total)
        line = f"{label}: {processed}/{total} ({percent:.0f}%)"
    else:
        line = label
    print("\r" + line.ljust(78), end="", flush=True)
    if state.get("status") in ("done", "error"):
        print()


def _choose_folder(folders):
    paths = folders.names()
    if not paths:
        print("Список папок пуст.")
        return None
    for index, path in enumerate(paths, start=1):
        status = "" if os.path.isdir(path) else "  [нет]"
        print(f"{index}. {display_folder(path)}{status}")
    raw = input("Номер папки (0 — отмена): ").strip()
    if not raw or raw == "0":
        return None
    try:
        index = int(raw) - 1
    except ValueError:
        print("Неверный номер.")
        return None
    if not 0 <= index < len(paths):
        print("Неверный номер.")
        return None
    return paths[index]


def _print_folders(system, folders):
    print("\n=== Музыкальные папки ===")
    print(
        "Библиотека собирается из этих папок; аудиофайлы не изменяются."
    )
    print(
        "Слежение в TUI: "
        + ("включено" if folders.watch_enabled else "выключено")
    )
    paths = folders.names()
    if not paths:
        print("Папок нет.")
        return
    tracks = list(system.table.values())
    for index, path in enumerate(paths, start=1):
        status = "" if os.path.isdir(path) else "  [нет]"
        count = len(tracks_in_folder(tracks, path))
        print(
            f"{index}. {display_folder(path)} — "
            f"{count} треков{status}"
        )


def _remove_folder_console(system, folders):
    path = _choose_folder(folders)
    if not path:
        return
    answer = input(
        "Убрать папку и её треки из библиотеки?\n"
        "Аудиофайлы на диске останутся. [y/N]: "
    ).strip().lower()
    if answer not in ("y", "yes", "д", "да"):
        return
    remaining = [item for item in folders.names() if item != path]
    removed_ids = []
    for track in list(system.table.values()):
        if not track.filepath or not path_is_within(track.filepath, path):
            continue
        if any(path_is_within(track.filepath, item) for item in remaining):
            continue
        if system.delete_track(track.id):
            removed_ids.append(str(track.id))
    folders.remove(path)
    playlists = PlaylistStore()
    for track_id in removed_ids:
        playlists.remove_track_everywhere(track_id)
    _save_quietly(system)
    print(f"Папка убрана. Удалено записей о треках: {len(removed_ids)}")


def _folders_menu_console(system, folders):
    while True:
        _print_folders(system, folders)
        print("\n1 - добавить папку")
        print("2 - пересканировать папку")
        print("3 - убрать папку из библиотеки")
        print("4 - включить/выключить слежение в TUI")
        print("0 - назад")
        choice = input("\n> ").strip().lower()
        if choice in ("0", "q", ""):
            return
        if choice in ("1", "a", "д"):
            _add_folder_console(system, folders)
        elif choice in ("2", "s", "с"):
            path = _choose_folder(folders)
            if path:
                print(
                    system.load_from_directory(
                        path,
                        progress=_console_analysis_progress,
                    )
                )
        elif choice in ("3", "d", "у"):
            _remove_folder_console(system, folders)
        elif choice in ("4", "w", "ц"):
            folders.set_watch_enabled(not folders.watch_enabled)
            print(
                "Слежение в TUI: "
                + ("включено" if folders.watch_enabled else "выключено")
            )
        else:
            print("Неизвестная команда.")


def main():
    system = MusicSystem()
    folders = FolderStore()
    if folders.error:
        print(folders.error)
    cache = _load_saved(system)
    if not cache["found"] or cache["error"]:
        print("Библиотека пуста. Добавьте музыку через TUI или пункт 12 (Музыкальные папки).")
    while True:
        print("\n==============================")
        print(" Музыкальная система")
        print("==============================")
        print("1 - Добавить трек")
        print("2 - Найти трек")
        print("3 - Удалить трек")
        print("4 - Добавить связь")
        print("5 - Показать сортировку")
        print("6 - Рекомендации")
        print("7 - Отчёт")
        print("8 - Загрузить треки")
        print("9 - Загрузить связи")
        print("10 - Показать все треки")
        print("11 - Поиск по названию")
        print("12 - Музыкальные папки и библиотека")
        print("13 - Показать анализ трека")
        print("14 - Пересчитать анализ загруженных")
        print("15 - Сохранить библиотеку и связи")
        print("16 - Загрузить сохранённую библиотеку")
        print("17 - Показать путь к сохранению")
        print("0 - Выход")
        choice = input("\n> ")
        if choice == "1":
            track_id = input("ID: ")
            title = input("Название: ")
            artist = input("Исполнитель: ")
            genre = input("Жанр: ")
            try:
                duration = int(input("Длительность: "))
                rating = int(input("Рейтинг: "))
            except ValueError:
                print("Неверный ввод.")
                continue
            track = Track(
                track_id,
                title,
                artist,
                genre,
                duration,
                rating
            )
            if system.add_track(track):
                _save_quietly(system)
            else:
                print("Трек с таким ID уже существует.")
        elif choice == "2":
            track_id = input("ID трека: ")
            track = system.find_track(track_id)
            if track:
                print(track)
            else:
                print("Не найден.")
        elif choice == "3":
            track_id = input("ID трека: ")
            if system.delete_track(track_id):
                _save_quietly(system)
            else:
                print("Трек не найден.")
        elif choice == "4":
            id1 = input("Первый трек: ")
            id2 = input("Второй трек: ")
            print(system.add_relation(id1, id2))
            _save_quietly(system)
        elif choice == "5":
            system.show_sorted()
        elif choice == "6":
            track_id = input("ID трека: ")
            recommendations = system.recommend(track_id)
            if recommendations:
                system.print_tracks(recommendations)
            else:
                print("Рекомендаций нет.")
        elif choice == "7":
            system.generate_report()
        elif choice == "8":
            filename = input("Файл треков: ")
            loaded = system.load_from_file(filename)
            print(f"Загружено треков: {loaded}")
            _save_quietly(system)
        elif choice == "9":
            filename = input("Файл связей: ")
            print(system.load_relations(filename))
            _save_quietly(system)
        elif choice == "10":
            system.show_all_tracks()
        elif choice == "11":
            query = input("Название: ")
            system.search_by_title(query)
        elif choice == "12":
            _folders_menu_console(system, folders)
        elif choice == "13":
            track_id = input("ID трека: ")
            print(system.show_analysis(track_id))
        elif choice == "14":
            print(
                system.reanalyze(
                    progress=_console_analysis_progress,
                )
            )
        elif choice == "15":
            try:
                result = system.save_to_disk()
                print(
                    f"Сохранено: {result['tracks']} треков, "
                    f"{result['edges']} связей\n{result['path']}"
                )
            except Exception as exc:
                print(f"Не удалось сохранить: {exc}")
        elif choice == "16":
            result = _load_saved(system)
            if result["found"] and not result["error"]:
                system.show_all_tracks()
        elif choice == "17":
            print(f"Сохранение: {default_library_path()}")
        elif choice == "0":
            _save_quietly(system)
            print("Выход...")
            break
        else:
            print("Неизвестная команда.")

if __name__ == "__main__":
    main()
