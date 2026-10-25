"""Аудио-анализ и построение графа похожести треков.

Что анализируется:

* метаданные — исполнитель, альбом, жанр, год, ключевые тональности;
* темп и ритм — BPM, onset rate, танцевальность;
* тональность — тоника, лад (major/minor), сила тональности;
* громкость и динамика — LUFS-подобная громкость, RMS, dynamic complexity;
* тембр — спектральный центроид, rolloff, ZCR, flux, flatness, HFC, MFCC;
* высокоуровневые признаки — энергия, valence/arousal, настроение,
  жанр по звучанию.

На основе этих признаков строится взвешенный граф: похожесть считается как
комбинация аудио-близости и близости метаданных. Это позволяет рекомендовать
как треки того же исполнителя/альбома, так и музыку, похожую по звучанию.

Essentia импортируется лениво: если её нет, остальная программа продолжает
работать, а при попытке анализа выдаётся понятная ошибка.
"""

from __future__ import annotations

import math
import os
import re

import numpy as np


ANALYSIS_VERSION = 1

GENRE_ALIASES = {
    "punk": ("Punk", "Rock"),
    "punk rock": ("Punk Rock", "Rock"),
    "punkrock": ("Punk Rock", "Rock"),
    "hardcore": ("Hardcore", "Rock"),
    "allrock": ("Rock", "Rock"),
    "rusrock": ("Russian Rock", "Rock"),
    "russian rock": ("Russian Rock", "Rock"),
    "rock": ("Rock", "Rock"),
    "grunge": ("Grunge", "Rock"),
    "ska": ("Ska", "Ska"),
    "folkrock": ("Folk Rock", "Folk"),
    "folk rock": ("Folk Rock", "Folk"),
    "folk": ("Folk", "Folk"),
    "metal": ("Metal", "Metal"),
    "heavy metal": ("Heavy Metal", "Metal"),
    "nu metal": ("Nu Metal", "Metal"),
    "thrash metal": ("Thrash Metal", "Metal"),
    "alternative": ("Alternative", "Alternative"),
    "local-indie": ("Indie", "Alternative"),
    "indie": ("Indie", "Alternative"),
    "pop": ("Pop", "Pop"),
    "dance": ("Dance", "Electronic"),
    "electronic": ("Electronic", "Electronic"),
    "edm": ("Electronic", "Electronic"),
    "hip-hop": ("Hip-Hop", "Hip-Hop"),
    "hip hop": ("Hip-Hop", "Hip-Hop"),
    "rap": ("Rap", "Hip-Hop"),
    "jazz": ("Jazz", "Jazz"),
    "blues": ("Blues", "Jazz"),
    "classical": ("Classical", "Classical"),
    "ambient": ("Ambient", "Ambient"),
    "soundtrack": ("Soundtrack", "Soundtrack"),
}

# Поля, которые участвуют в аудио-похожести, и их веса.
AUDIO_VECTOR_FIELDS = (
    ("bpm", 1.0),
    ("loudness_db", 1.0),
    ("rms_db", 0.6),
    ("spectral_centroid", 0.9),
    ("spectral_rolloff", 0.7),
    ("zcr", 0.9),
    ("spectral_flux", 0.8),
    ("flatness_db", 0.5),
    ("hfc", 0.5),
    ("onset_rate", 0.8),
    ("dance_norm", 1.0),
    ("energy", 1.3),
    ("valence", 0.8),
    ("arousal", 1.1),
    ("dynamic_complexity", 0.5),
    ("key_strength", 0.3),
    ("tempo_norm", 1.0),
    ("brightness", 0.8),
    ("beat_confidence", 0.3),
)

_ES = None


def _get_essentia():
    """Ленивый импорт Essentia с понятной ошибкой при отсутствии пакета."""
    global _ES
    if _ES is not None:
        return _ES

    try:
        import essentia

        # Не засоряем консоль служебными INFO-сообщениями.
        try:
            essentia.log.infoActive = False
        except Exception:
            pass

        import essentia.standard as es
    except ImportError as exc:
        raise RuntimeError(
            "Для анализа аудио нужен пакет essentia. "
            "Установите его в окружение проекта: pip install essentia"
        ) from exc

    _ES = es
    return es


def clamp(value, low=0.0, high=1.0):
    return float(max(low, min(high, value)))


def _finite(value, default=0.0):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) else default


def _normalize_text(value):
    value = (value or "").strip().casefold()
    value = re.sub(r"[\W_]+", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def normalize_genre(raw_genre):
    """Возвращает (канонический жанр, широкое семейство жанров)."""
    raw = (raw_genre or "").replace("\ufeff", "").strip()
    if not raw:
        return "", ""

    key = re.sub(r"[\s_]+", " ", raw.casefold()).strip()
    if key in GENRE_ALIASES:
        return GENRE_ALIASES[key]

    if "punk" in key:
        return "Punk", "Rock"
    if "metal" in key:
        return "Metal", "Metal"
    if "rock" in key:
        return "Rock", "Rock"
    if "indie" in key:
        return "Indie", "Alternative"
    if "ska" in key:
        return "Ska", "Ska"
    if "folk" in key:
        return "Folk", "Folk"
    if "pop" in key:
        return "Pop", "Pop"
    if "electro" in key or "dance" in key or "edm" in key:
        return "Electronic", "Electronic"
    if "hip" in key or "rap" in key:
        return "Hip-Hop", "Hip-Hop"
    if "jazz" in key or "blues" in key or "swing" in key:
        return "Jazz/Blues", "Jazz"
    if "classic" in key or "orchestra" in key or "opera" in key:
        return "Classical", "Classical"
    if "ambient" in key or "chill" in key:
        return "Ambient", "Ambient"

    display = raw[0].upper() + raw[1:] if raw else ""
    return display, display


def infer_audio_genre(features):
    """Оценивает жанр по звучанию (эвристика на DSP-признаках)."""
    bpm = _finite(features.get("bpm"))
    energy = _finite(features.get("energy"))
    dance = _finite(features.get("dance_norm"))
    zcr = _finite(features.get("zcr"))
    brightness = _finite(features.get("brightness"))
    loudness = _finite(features.get("loud_norm"))
    percussiveness = _finite(features.get("percussiveness"))

    if bpm <= 0:
        return "Unknown", "Unknown"

    if energy < 0.40 and bpm < 105 and brightness < 0.35:
        return "Ambient/Classical", "Classical"

    if energy < 0.52 and bpm < 110 and zcr < 0.065:
        return "Jazz/Blues", "Jazz"

    # Высокая энергия + шумный спектр — обычно гитарный рок/панк,
    # даже если ритм-детектор ошибся и показал половинный темп.
    if energy >= 0.63 and zcr >= 0.10 and percussiveness >= 0.40:
        return "Punk/Metal", "Rock"

    if (
        70 <= bpm <= 105
        and dance >= 0.70
        and energy < 0.60
        and zcr < 0.08
        and percussiveness < 0.55
    ):
        return "Hip-Hop/R&B", "Hip-Hop"

    if dance >= 0.72 and bpm < 130 and brightness >= 0.35 and zcr < 0.10:
        return "Pop/Dance", "Pop"

    if bpm >= 145 and energy >= 0.64 and (zcr >= 0.06 or percussiveness >= 0.50):
        return "Punk/Metal", "Rock"

    if energy >= 0.68 or loudness >= 0.62:
        return "Rock", "Rock"

    if bpm >= 120 and zcr < 0.06 and brightness < 0.40:
        return "Folk/Rock", "Folk"

    if dance >= 0.65 and brightness >= 0.50 and zcr < 0.08:
        return "Pop", "Pop"

    return "Rock", "Rock"


def infer_mood(valence, arousal):
    """Четыре квадранта настроения по valence/arousal."""
    if arousal >= 0.55 and valence >= 0.55:
        return "весёлое", "happy"
    if arousal >= 0.55:
        return "агрессивное", "aggressive"
    if valence >= 0.55:
        return "спокойное", "calm"
    return "грустное", "sad"


def _safe_call(name, func, default, errors):
    try:
        return func()
    except Exception as exc:  # Essentia может падать на битых файлах
        errors.append(f"{name}: {exc}")
        return default


def extract_features(filepath, sample_rate=22050):
    """Извлекает полный набор аудио-признаков из файла."""
    if not filepath or not os.path.isfile(filepath):
        raise FileNotFoundError(f"Файл не найден: {filepath}")

    es = _get_essentia()
    errors = []

    audio = es.MonoLoader(filename=filepath, sampleRate=sample_rate)()
    duration = len(audio) / float(sample_rate)
    if duration < 2.0:
        raise ValueError(f"Слишком короткий аудиофайл: {filepath}")

    bpm, beats, beat_confidence, _, _ = _safe_call(
        "rhythm",
        lambda: es.RhythmExtractor2013(method="multifeature")(audio),
        (0.0, np.array([]), 0.0, np.array([]), np.array([])),
        errors,
    )
    key, scale, key_strength = _safe_call(
        "key",
        lambda: es.KeyExtractor()(audio),
        ("", "", 0.0),
        errors,
    )
    danceability, _ = _safe_call(
        "danceability",
        lambda: es.Danceability()(audio),
        (0.5, np.array([])),
        errors,
    )
    _, onset_rate = _safe_call(
        "onset_rate",
        lambda: es.OnsetRate()(audio),
        (np.array([]), 0.0),
        errors,
    )
    dynamic_complexity, loudness_db = _safe_call(
        "dynamic_complexity",
        lambda: es.DynamicComplexity()(audio),
        (0.0, -30.0),
        errors,
    )

    window = es.Windowing(type="hann")
    spectrum = es.Spectrum()
    spectral_centroid = es.SpectralCentroidTime()
    spectral_rolloff = es.RollOff()
    zero_crossing_rate = es.ZeroCrossingRate()
    spectral_flux = es.Flux()
    flatness = es.FlatnessDB()
    rms = es.RMS()
    hfc = es.HFC()
    mfcc_algo = es.MFCC()
    peaks_algo = es.SpectralPeaks()
    hpcp_algo = es.HPCP()

    centroids = []
    rolloffs = []
    zcrs = []
    fluxes = []
    flatnesses = []
    rms_values = []
    hfc_values = []
    mfcc_values = []
    chroma_values = []

    for frame in es.FrameGenerator(
        audio, frameSize=2048, hopSize=1024, startFromZero=True
    ):
        spec = spectrum(window(frame))
        centroids.append(spectral_centroid(frame))
        rolloffs.append(spectral_rolloff(spec))
        zcrs.append(zero_crossing_rate(frame))
        fluxes.append(spectral_flux(spec))
        flatnesses.append(flatness(spec))
        rms_values.append(rms(frame))
        hfc_values.append(hfc(spec))

        try:
            mfcc_values.append(mfcc_algo(spec)[1])
        except Exception as exc:
            errors.append(f"mfcc: {exc}")

        try:
            freqs, magnitudes = peaks_algo(spec)
            if len(freqs):
                chroma_values.append(hpcp_algo(freqs, magnitudes))
        except Exception as exc:
            errors.append(f"hpcp: {exc}")

    if not rms_values:
        raise ValueError(f"Не удалось разобрать аудио: {filepath}")

    def scalar_stats(values):
        arr = np.asarray(values, dtype=float)
        arr = arr[np.isfinite(arr)]
        if arr.size == 0:
            return 0.0, 0.0
        return float(arr.mean()), float(arr.std())

    def vector_stats(values, size):
        if not values:
            return [0.0] * size, [0.0] * size
        arr = np.asarray(values, dtype=float)
        arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
        return (
            [float(x) for x in arr.mean(axis=0)],
            [float(x) for x in arr.std(axis=0)],
        )

    centroid_mean, centroid_std = scalar_stats(centroids)
    rolloff_mean, _ = scalar_stats(rolloffs)
    zcr_mean, zcr_std = scalar_stats(zcrs)
    flux_mean, _ = scalar_stats(fluxes)
    flatness_mean, _ = scalar_stats(flatnesses)
    rms_mean, rms_std = scalar_stats(rms_values)
    hfc_mean, _ = scalar_stats(hfc_values)
    mfcc_mean, mfcc_std = vector_stats(mfcc_values, 13)
    chroma_mean, _ = vector_stats(chroma_values, 12)

    rms_db = 20.0 * math.log10(max(rms_mean, 1e-9))
    rms_db = max(rms_db, -80.0)
    loudness_db = _finite(loudness_db, -30.0)
    loudness_db = max(loudness_db, -80.0)

    bpm = _finite(bpm)
    beat_confidence = _finite(beat_confidence)
    key_strength = clamp(_finite(key_strength))
    danceability = _finite(danceability, 0.5)
    onset_rate = _finite(onset_rate)
    dynamic_complexity = _finite(dynamic_complexity)
    zcr_mean = _finite(zcr_mean)

    tempo_norm = clamp((bpm - 50.0) / 140.0)
    loud_norm = clamp((loudness_db + 35.0) / 35.0)
    rms_norm = clamp((rms_db + 45.0) / 45.0)
    brightness = clamp((centroid_mean - 400.0) / 4000.0)
    percussiveness = clamp(onset_rate / 8.0)
    dance_norm = clamp(danceability / 1.5)
    zcr_norm = clamp(zcr_mean / 0.25)

    energy = clamp(
        0.28 * tempo_norm
        + 0.24 * loud_norm
        + 0.16 * rms_norm
        + 0.14 * brightness
        + 0.10 * percussiveness
        + 0.08 * dance_norm
    )
    arousal = clamp(
        0.45 * energy
        + 0.22 * tempo_norm
        + 0.13 * percussiveness
        + 0.12 * loud_norm
        + 0.08 * zcr_norm
    )

    mode = 1.0 if scale == "major" else 0.0
    valence = clamp(
        0.32 * mode
        + 0.22 * dance_norm
        + 0.14 * brightness
        + 0.12 * key_strength
        + 0.10 * (1.0 - abs(tempo_norm - 0.5) * 2.0)
        + 0.10 * (1.0 - zcr_norm)
    )

    mood, mood_en = infer_mood(valence, arousal)

    features = {
        "version": ANALYSIS_VERSION,
        "duration_audio": float(duration),
        "bpm": bpm,
        "beat_count": int(len(beats)),
        "beat_confidence": beat_confidence,
        "onset_rate": onset_rate,
        "danceability": danceability,
        "dance_norm": dance_norm,
        "dynamic_complexity": dynamic_complexity,
        "loudness_db": loudness_db,
        "rms_mean": rms_mean,
        "rms_std": rms_std,
        "rms_db": rms_db,
        "spectral_centroid": centroid_mean,
        "spectral_centroid_std": centroid_std,
        "spectral_rolloff": rolloff_mean,
        "zcr": zcr_mean,
        "zcr_std": zcr_std,
        "spectral_flux": flux_mean,
        "flatness_db": flatness_mean,
        "hfc": hfc_mean,
        "key": key,
        "scale": scale,
        "key_strength": key_strength,
        "mfcc_mean": mfcc_mean,
        "mfcc_std": mfcc_std,
        "chroma_mean": chroma_mean,
        "tempo_norm": tempo_norm,
        "loud_norm": loud_norm,
        "rms_norm": rms_norm,
        "brightness": brightness,
        "percussiveness": percussiveness,
        "zcr_norm": zcr_norm,
        "energy": energy,
        "arousal": arousal,
        "valence": valence,
        "mood": mood,
        "mood_en": mood_en,
    }

    audio_genre, audio_genre_family = infer_audio_genre(features)
    features["audio_genre"] = audio_genre
    features["audio_genre_family"] = audio_genre_family
    features["errors"] = errors
    return features


def apply_analysis(track, features):
    """Записывает результат анализа в поля Track."""
    track.analysis = features
    track.bpm = _finite(features.get("bpm"))
    track.key = features.get("key") or None
    track.scale = features.get("scale") or None
    track.key_strength = _finite(features.get("key_strength"))
    track.loudness_db = _finite(features.get("loudness_db"))
    track.dynamic_complexity = _finite(features.get("dynamic_complexity"))
    track.spectral_centroid = _finite(features.get("spectral_centroid"))
    track.spectral_rolloff = _finite(features.get("spectral_rolloff"))
    track.zcr = _finite(features.get("zcr"))
    track.spectral_flux = _finite(features.get("spectral_flux"))
    track.onset_rate = _finite(features.get("onset_rate"))
    track.danceability = _finite(features.get("dance_norm"), 0.5)
    track.energy = _finite(features.get("energy"))
    track.valence = _finite(features.get("valence"))
    track.arousal = _finite(features.get("arousal"))
    track.mood = features.get("mood")
    track.audio_genre = features.get("audio_genre")
    track.audio_genre_family = features.get("audio_genre_family")

    metadata_genre = getattr(track, "metadata_genre", track.genre)
    display_genre, family = normalize_genre(metadata_genre)
    features["metadata_genre"] = display_genre
    if not display_genre:
        display_genre = track.audio_genre or "Unknown"
        family = track.audio_genre_family or "Unknown"
    track.genre = display_genre
    track.genre_family = family

    if not track.duration:
        track.duration = int(round(_finite(features.get("duration_audio"))))

    return track


def analyze_library(system, progress=None, force=False):
    """Анализирует все загруженные с диска треки.

    progress(index, total, track, ok, error) вызывается после каждого трека.
    """
    tracks = [
        track for track in system.table.values()
        if track.filepath
    ]
    stats = {
        "total": len(tracks),
        "analyzed": 0,
        "skipped": 0,
        "failed": 0,
        "errors": [],
    }

    for index, track in enumerate(tracks, start=1):
        if (
            track.analysis
            and not force
            and track.analysis.get("version") == ANALYSIS_VERSION
        ):
            stats["skipped"] += 1
            if progress:
                progress(index, len(tracks), track, True, None)
            continue

        try:
            features = extract_features(track.filepath)
            apply_analysis(track, features)
            stats["analyzed"] += 1
            error = None
        except Exception as exc:
            stats["failed"] += 1
            error = f"{track.artist} - {track.title}: {exc}"
            stats["errors"].append(error)

        if progress:
            progress(index, len(tracks), track, error is None, error)

    return stats


def metadata_similarity(a, b):
    """Похожесть по метаданным: исполнитель, альбом, жанр, настроение и т.д."""
    score = 0.0

    artist_a = _normalize_text(a.artist)
    artist_b = _normalize_text(b.artist)
    if artist_a and artist_a == artist_b and artist_a != "unknown":
        score += 0.32

    album_a = _normalize_text(a.album)
    album_b = _normalize_text(b.album)
    if album_a and album_a == album_b and album_a != "unknown":
        score += 0.30

    genre_a = (a.genre_family or "").casefold()
    genre_b = (b.genre_family or "").casefold()
    if genre_a and genre_a == genre_b and genre_a != "unknown":
        score += 0.22
        if (a.genre or "").casefold() == (b.genre or "").casefold():
            score += 0.08

    if (
        a.audio_genre_family
        and a.audio_genre_family != "Unknown"
        and a.audio_genre_family == b.audio_genre_family
    ):
        score += 0.05

    # Настроение — отдельный сильный сигнал: треки одного квадранта
    # valence/arousal должны связываться чаще, даже если жанры разные.
    mood_a = _normalize_text(a.mood)
    mood_b = _normalize_text(b.mood)
    if mood_a and mood_a == mood_b:
        score += 0.06

    if a.year and b.year:
        if a.year == b.year:
            score += 0.04
        elif abs(a.year - b.year) <= 5:
            score += 0.02

    if a.key and b.key and a.key == b.key:
        score += 0.03
        if a.scale and a.scale == b.scale:
            score += 0.02

    title_a = _normalize_text(a.title)
    title_b = _normalize_text(b.title)
    if title_a and title_a == title_b:
        score += 0.05

    return min(score, 1.0)


def _audio_matrix(tracks):
    """Нормированная матрица аудио-признаков (z-score по библиотеке)."""
    rows = []
    for track in tracks:
        analysis = track.analysis or {}
        rows.append([
            _finite(analysis.get(field))
            for field, _ in AUDIO_VECTOR_FIELDS
        ])

    matrix = np.asarray(rows, dtype=float)
    if matrix.size == 0:
        return matrix

    mean = matrix.mean(axis=0)
    std = matrix.std(axis=0)
    std[std < 1e-9] = 1.0
    matrix = (matrix - mean) / std

    weights = np.asarray([weight for _, weight in AUDIO_VECTOR_FIELDS])
    matrix = matrix * weights

    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms < 1e-9] = 1.0
    return matrix / norms


def feature_similarity_scores(system, seed_id, metadata_weight=0.3):
    """Похожесть всех проанализированных треков на ``seed_id``.

    Это не обход графа, а прямое сравнение характеристик: нормированные
    аудио-признаки (косинус) плюс метаданные. Возвращает словарь
    ``{track_id: score}`` без самого seed-трека.
    """
    tracks = [
        track for track in system.table.values()
        if track.analysis
    ]
    if len(tracks) < 2:
        return {}

    seed_index = next(
        (
            index
            for index, track in enumerate(tracks)
            if str(track.id) == str(seed_id)
        ),
        None,
    )
    if seed_index is None:
        return {}

    matrix = _audio_matrix(tracks)
    if matrix.size == 0:
        return {}

    seed_vector = matrix[seed_index]
    audio_scores = (matrix @ seed_vector + 1.0) / 2.0
    seed_track = tracks[seed_index]
    scores = {}
    for index, track in enumerate(tracks):
        if index == seed_index:
            continue
        audio_score = clamp(audio_scores[index], 0.0, 1.0)
        meta_score = metadata_similarity(seed_track, track)
        scores[str(track.id)] = clamp(
            (1.0 - metadata_weight) * audio_score
            + metadata_weight * meta_score,
            0.0,
            1.0,
        )
    return scores


def build_similarity_graph(
    system,
    top_k=6,
    metadata_weight=0.5,
    threshold=0.0,
    progress=None,
):
    """Строит граф похожести и возвращает статистику.

    Для каждой вершины добавляются top_k самых похожих треков, а также
    явные связи по альбому и исполнителю. Вес ребра — комбинированная
    похожесть (аудио + метаданные).

    ``progress(phase, processed, total, edges)`` вызывается по ходу работы.
    """
    tracks = [
        track for track in system.table.values()
        if track.analysis
    ]
    stats = {
        "tracks": len(tracks),
        "edges": 0,
        "audio_edges": 0,
        "metadata_edges": 0,
        "average_weight": 0.0,
    }
    # Полная сборка всегда заменяет граф, а не дополняет старые рёбра:
    # иначе после изменения весов в библиотеке остались бы устаревшие
    # связи, которых уже нет в новой top_k-выборке.
    system.graph.clear_edges()

    if len(tracks) < 2:
        if progress:
            progress("мало данных", 1, 1, 0)
        return stats

    n = len(tracks)
    if progress:
        progress("матрица", 0, n, 0)
    matrix = _audio_matrix(tracks)
    if matrix.size == 0:
        if progress:
            progress("матрица", n, n, 0)
        return stats

    # Косинусная близость в [-1, 1] переводится в [0, 1].
    audio_scores = (matrix @ matrix.T + 1.0) / 2.0
    if progress:
        progress("матрица", n, n, 0)

    metadata_scores = np.zeros((n, n), dtype=float)
    total_pairs = n * (n - 1) // 2
    processed_pairs = 0
    if progress:
        progress("метаданные", 0, total_pairs, 0)
    for i in range(n):
        for j in range(i + 1, n):
            value = metadata_similarity(tracks[i], tracks[j])
            metadata_scores[i, j] = value
            metadata_scores[j, i] = value
        processed_pairs += i
        if progress:
            progress(
                "метаданные",
                processed_pairs,
                total_pairs,
                len(system.graph.edges),
            )

    combined = (
        (1.0 - metadata_weight) * audio_scores
        + metadata_weight * metadata_scores
    )

    # 1) Ближайшие соседи по звучанию/метаданным.
    if progress:
        progress("связи", 0, n, len(system.graph.edges))
    for i, track in enumerate(tracks):
        order = np.argsort(-combined[i])
        added = 0
        for j in order:
            j = int(j)
            if j == i:
                continue
            weight = float(combined[i, j])
            if weight < threshold:
                continue
            system.connect_tracks(track.id, tracks[j].id, weight)
            stats["audio_edges"] += 1
            added += 1
            if added >= top_k:
                break
        if progress:
            progress(
                "связи",
                i + 1,
                n,
                len(system.graph.edges),
            )

    # 2) Явные связи по метаданным, чтобы один исполнитель/альбом был связан
    #    даже при непохожем звучании.
    for i in range(n):
        for j in range(i + 1, n):
            same_album = (
                tracks[i].album
                and tracks[i].album != "Unknown"
                and _normalize_text(tracks[i].album) == _normalize_text(tracks[j].album)
            )
            same_artist = (
                tracks[i].artist
                and _normalize_text(tracks[i].artist) == _normalize_text(tracks[j].artist)
                and _normalize_text(tracks[i].artist) != "unknown"
            )
            if not same_album and not same_artist:
                continue

            weight = float(combined[i, j])
            if same_album:
                weight = max(weight, 0.82)
            elif same_artist:
                weight = max(weight, 0.62)
            system.connect_tracks(tracks[i].id, tracks[j].id, weight)
            stats["metadata_edges"] += 1

    # Статистика по фактическому графу (без дубликатов).
    relevant = {
        track.id: track for track in tracks
    }
    weights = []
    for edge in system.graph.edges:
        if edge["from"] in relevant and edge["to"] in relevant:
            weights.append(edge["weight"])
    stats["edges"] = len(weights)
    stats["average_weight"] = (
        sum(weights) / len(weights) if weights else 0.0
    )
    if progress:
        progress("готово", n, n, stats["edges"])
    return stats


def _graph_stats(system, tracks=None):
    """Статистика по фактическим рёбрам графа."""
    relevant = (
        {str(track.id) for track in tracks}
        if tracks is not None
        else None
    )
    weights = []
    for edge in system.graph.edges:
        if relevant is not None and (
            str(edge["from"]) not in relevant
            or str(edge["to"]) not in relevant
        ):
            continue
        weights.append(float(edge["weight"]))
    return {
        "edges": len(weights),
        "average_weight": (
            sum(weights) / len(weights) if weights else 0.0
        ),
    }


def update_similarity_graph(
    system,
    track_ids,
    top_k=6,
    metadata_weight=0.5,
    threshold=0.0,
    progress=None,
):
    """Обновляет граф только для добавленных/изменённых треков.

    Полная пересборка графа на 1.5k треков занимает десятки секунд из-за
    попарного сравнения метаданных. При слежении за папками достаточно
    пересчитать рёбра новых треков: существующие рёбра не меняются.
    """
    changed_ids = {str(track_id) for track_id in track_ids}
    stats = {
        "tracks": 0,
        "edges": 0,
        "audio_edges": 0,
        "metadata_edges": 0,
        "average_weight": 0.0,
    }
    if not changed_ids:
        current = _graph_stats(system)
        stats.update(current)
        stats["tracks"] = sum(
            1 for track in system.table.values() if track.analysis
        )
        return stats

    tracks = [
        track for track in system.table.values()
        if track.analysis
    ]
    if len(tracks) < 2:
        if progress:
            progress("мало данных", 1, 1, 0)
        stats["tracks"] = len(tracks)
        return stats

    # Сначала убираем старые рёбра изменённых треков. Для нового трека
    # их нет, для изменённого — это гарантирует, что старые признаки не
    # останутся в графе.
    for track in system.table.values():
        if str(track.id) in changed_ids:
            system.graph.remove_incident_edges(track.id)

    changed = [
        track for track in tracks
        if str(track.id) in changed_ids
    ]
    if not changed:
        current = _graph_stats(system, tracks)
        stats.update(current)
        stats["tracks"] = len(tracks)
        return stats

    matrix = _audio_matrix(tracks)
    index_by_id = {
        str(track.id): index for index, track in enumerate(tracks)
    }
    if progress:
        progress("связи", 0, len(changed), len(system.graph.edges))

    for position, track in enumerate(changed, start=1):
        index = index_by_id[str(track.id)]
        audio_scores = (matrix @ matrix[index] + 1.0) / 2.0
        combined = np.empty(len(tracks), dtype=float)
        for other_index, other in enumerate(tracks):
            metadata_score = metadata_similarity(track, other)
            combined[other_index] = (
                (1.0 - metadata_weight)
                * clamp(audio_scores[other_index], 0.0, 1.0)
                + metadata_weight * metadata_score
            )

        # Ближайшие соседи по звучанию/метаданным.
        order = np.argsort(-combined)
        added = 0
        for other_index in order:
            other_index = int(other_index)
            if other_index == index:
                continue
            weight = float(combined[other_index])
            if weight < threshold:
                continue
            system.connect_tracks(track.id, tracks[other_index].id, weight)
            stats["audio_edges"] += 1
            added += 1
            if added >= top_k:
                break

        # Явные связи по альбому/исполнителю. Они не зависят от top_k и
        # позволяют рекомендациям находить тот же альбом даже при
        # непохожем звучании.
        for other_index, other in enumerate(tracks):
            if other_index == index:
                continue
            same_album = (
                track.album
                and track.album != "Unknown"
                and _normalize_text(track.album)
                == _normalize_text(other.album)
            )
            same_artist = (
                track.artist
                and _normalize_text(track.artist)
                == _normalize_text(other.artist)
                and _normalize_text(track.artist) != "unknown"
            )
            if not same_album and not same_artist:
                continue
            weight = float(combined[other_index])
            if same_album:
                weight = max(weight, 0.82)
            elif same_artist:
                weight = max(weight, 0.62)
            system.connect_tracks(track.id, other.id, weight)
            stats["metadata_edges"] += 1

        if progress:
            progress(
                "связи",
                position,
                len(changed),
                len(system.graph.edges),
            )

    stats["tracks"] = len(tracks)
    stats.update(_graph_stats(system, tracks))
    if progress:
        progress("готово", len(changed), len(changed), stats["edges"])
    return stats


def format_analysis(track):
    """Человекочитаемое описание анализа трека."""
    metadata_genre = track.genre
    if track.analysis:
        metadata_genre = track.analysis.get("metadata_genre") or track.genre

    lines = [
        f"Исполнитель: {track.artist}",
        f"Название: {track.title}",
        f"Альбом: {track.album or 'Unknown'}",
        f"Жанр (метаданные): {metadata_genre or 'не указан'}",
    ]
    if track.year:
        lines.append(f"Год: {track.year}")
    if track.track_number:
        lines.append(f"Номер в альбоме: {track.track_number}")

    if not track.analysis:
        lines.append("")
        lines.append("Аудио-анализ не выполнен.")
        if track.filepath:
            lines.append("Загрузите папку заново или запустите анализ.")
        return "\n".join(lines)

    analysis = track.analysis
    lines.extend([
        "",
        "=== Аудио-анализ ===",
        f"BPM: {track.bpm:.1f}",
        f"Тональность: {track.key or '?'} {track.scale or ''}".rstrip(),
        f"Сила тональности: {track.key_strength:.2f}",
        f"Громкость: {track.loudness_db:.1f} dB",
        f"Dynamic complexity: {track.dynamic_complexity:.2f}",
        f"Спектральный центроид: {track.spectral_centroid:.0f} Hz",
        f"Спектральный rolloff: {track.spectral_rolloff:.0f} Hz",
        f"Zero-crossing rate: {track.zcr:.3f}",
        f"Onset rate: {track.onset_rate:.2f}/s",
        f"Танцевальность: {track.danceability:.2f}",
        f"Энергия: {track.energy:.2f}",
        f"Valence: {track.valence:.2f}",
        f"Arousal: {track.arousal:.2f}",
        f"Настроение: {track.mood}",
        f"Жанр по звучанию: {track.audio_genre}",
    ])
    if analysis.get("errors"):
        lines.append("")
        lines.append("Предупреждения: " + "; ".join(analysis["errors"][:3]))
    if track.filepath:
        lines.append("")
        lines.append(f"Файл: {track.filepath}")
    return "\n".join(lines)
