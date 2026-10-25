# TUI Music

A terminal player for a local music library. I keep my music as files, so
there is no streaming, account or online metadata lookup here. Point the app
at a few folders and it reads tags with `ffprobe`, extracts audio features
with Essentia and keeps the result in JSON. Playback uses mpv, the UI is
Textual.

The interface is in Russian for now. This README is in English.

## Requirements

- Python 3.10 or newer
- `mpv` for playback
- `ffprobe` (from FFmpeg) for tags and durations
- `textual`, `rich` and `numpy` from pip
- Essentia for audio analysis

```sh
python -m venv .venv
source .venv/bin/activate
pip install textual rich numpy
```

Install `mpv` and `ffmpeg` with your package manager. On Arch that is
`sudo pacman -S mpv ffmpeg`, on Debian/Ubuntu it is `sudo apt install mpv ffmpeg`.

Essentia is the awkward dependency. Playback and folder import work without
it, but analysis and the similarity graph need it. Install it separately and
check `python -c "import essentia"` before blaming the app. The package itself
is not small.

If mpv is missing, the app still starts and lets you browse the library; it
just cannot play anything.

## Running

```sh
python music_tui.py
```

Start by pressing `o` (or the ПАПКИ button) and adding a folder. The first
scan is slow: ffprobe reads every file, then Essentia decodes it for the
feature graph. On a large collection, expect it to take a while. Results are
cached in `library.json`, so later scans skip tracks that are already there.

If you want to know what a long scan is doing, open another terminal:

```sh
python music_tui.py --status
```

`./dist/music_tui --status` does the same if you built the binary.

## Keys

| Key | Action |
| --- | --- |
| `Space` | play / pause |
| `Enter` | play the selected item |
| `e` | add selected track to the queue |
| `s` / `S` | toggle shuffle / shuffle the queue |
| `l` | repeat: off, all, one |
| `r` / `R` | recommendations / radio from selected track |
| `o` | folders and watch settings |
| `i` | audio analysis for selected track |
| `p` / `P` | playlists tab / playlist manager |
| `u` | show the queue |
| `x` | stop |
| `F1` | full help |
| `F2` | switch ANSI light/dark theme |
| `q` | quit |

The on-screen buttons cover the same player actions if you prefer the mouse.

## Data

By default everything is in `~/.local/share/tui-music-recomendator` (or
`$XDG_DATA_HOME/tui-music-recomendator` if that variable is set):

- `library.json`: tracks, cached analysis and similarity graph
- `folders.json`: folders being watched
- `playlists.json`
- `analysis_state.json`: progress for `--status`
- `analysis.log`

Set `TUI_MUSIC_DATA_DIR` if you want to keep that somewhere else. Audio files
are only read, never modified. Removing a folder from the library drops its
entries from the cache, not the folder from disk.

## Build

```sh
pip install pyinstaller
pyinstaller --clean --noconfirm music_tui.spec
```

The binary ends up in `dist/music_tui`. The spec explicitly collects Textual's
widget submodules, because PyInstaller cannot see imports that Textual
resolves lazily.

## Code map

- `music_tui.py`: the Textual app, screens and key handling
- `playback.py`: mpv IPC and the queue/shuffle/repeat controller
- `main.py`: the library model, graph and old console interface
- `audio_loader.py`: folder walking and ffprobe metadata
- `audio_analysis.py`: Essentia features and similarity graph
- `folder_store.py`, `folder_watcher.py`: saved folders and change polling
- `library_store.py`, `playlist_store.py`: JSON persistence
- `app_paths.py`: XDG paths
- `analysis_tracker.py`: the state file used by `--status`

## Rough edges

The first analysis pass is the expensive part, and a fresh import does not
show up in the library until it finishes. Recommendations are only as good as
the tags and audio features they get. Essentia brings in its own native
dependencies, so it is the most likely thing to break on a new machine.

I only run this on Linux. It may work on other Unix-like systems with mpv and
ffprobe installed, but I have not tested that.
