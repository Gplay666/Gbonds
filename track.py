class Track:
    """Модель трека.

    Поля анализа (bpm, key, mood и т.д.) заполняются модулем audio_analysis
    после загрузки файла с диска. Для треков, добавленных вручную, они
    остаются None.
    """

    def __init__(
        self,
        track_id,
        title,
        artist,
        genre,
        duration,
        rating,
        filepath=None,
        album=None,
        year=None,
        track_number=None,
        file_mtime_ns=None,
        file_size=None,
    ):
        self.id = track_id
        self.title = title
        self.artist = artist
        self.genre = genre
        self.metadata_genre = genre
        self.duration = duration
        self.rating = rating
        self.filepath = filepath
        self.album = album
        self.year = year
        self.track_number = track_number
        self.file_mtime_ns = file_mtime_ns
        self.file_size = file_size

        # Результаты аудио-анализа
        self.analysis = None
        self.bpm = None
        self.key = None
        self.scale = None
        self.key_strength = None
        self.loudness_db = None
        self.dynamic_complexity = None
        self.spectral_centroid = None
        self.spectral_rolloff = None
        self.zcr = None
        self.spectral_flux = None
        self.onset_rate = None
        self.danceability = None
        self.energy = None
        self.valence = None
        self.arousal = None
        self.mood = None
        self.audio_genre = None
        self.genre_family = None
        self.audio_genre_family = None

    @property
    def has_analysis(self):
        return bool(self.analysis)

    def __repr__(self):
        parts = [
            f"[{self.id}]",
            f"{self.artist} - {self.title}",
            f"| {self.genre or 'Unknown'}",
        ]
        if self.album and self.album != "Unknown":
            parts.append(f"| {self.album}")
        parts.append(f"| {self.duration}s")
        parts.append(f"| rating={self.rating}")
        if self.has_analysis:
            parts.append(f"| {self.bpm:.0f} BPM")
            if self.key:
                parts.append(f"| {self.key} {self.scale}")
            if self.mood:
                parts.append(f"| {self.mood}")
        return " ".join(parts)
