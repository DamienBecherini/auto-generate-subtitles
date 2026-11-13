"""A folder as the source: inventory of the videos and of the subtitles already next to them, language of the audio
track (ffprobe tag when there is one), and batch file to resume a long piece of work after an interruption.

Subtitles recognised next to a video: `<name>.<code>.srt` or `.vtt` (code of 2 or 3 letters, variants "fr-FR" or
"pt-BR" included), and `<name>.srt` (unknown language, "?").
"""
import json
import os
import re
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import i18n, source
from .states import ScanState

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v", ".mpg", ".mpeg", ".ts", ".wmv", ".flv"}
SUBTITLE_EXTENSIONS = {".srt", ".vtt"}
BATCH_FILE_NAME = "subtitles.batch.json"
MUXED_SUFFIX = ".multi.mkv"                    # multi-track MKV made by the mux step: not a video to process
WORK_DIR = ".work"                             # working folders, inside the output folder
_CODE = re.compile(r"^[a-z]{2,3}(?:-[a-zA-Z]{2,4})?$")
LANGUAGE_TAGS = {"eng": "en", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de", "spa": "es", "ita": "it", "por": "pt", "dut": "nl",
                 "nld": "nl", "pol": "pl", "rus": "ru", "jpn": "ja", "chi": "zh", "zho": "zh", "kor": "ko", "ara": "ar", "tur": "tr"}


def subtitle_language_code(video_stem: str, file: Path) -> str | None:
    """Language code of a subtitle file next to the video, "?" without a code, None if it is not this video's."""
    if file.suffix.lower() not in SUBTITLE_EXTENSIONS:
        return None
    rest = file.name[:-len(file.suffix)]
    if rest == video_stem:
        return "?"
    if not rest.startswith(video_stem + "."):
        return None
    code = rest[len(video_stem) + 1:]
    return code.split("-")[0].lower() if _CODE.match(code) else None


def track_language(video: Path) -> str:
    """Language tag of the first audio track (container metadata), 2-letter code or ""."""
    try:
        r = subprocess.run([source.tool("ffprobe"), "-v", "error", "-select_streams", "a:0", "-show_entries", "stream_tags=language",
                            "-of", "json", str(video)], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        tag = (json.loads(r.stdout or "{}").get("streams") or [{}])[0].get("tags", {}).get("language", "").lower()
    except (source.SourceError, subprocess.SubprocessError, ValueError, OSError):
        return ""
    if not tag or tag == "und":
        return ""
    return LANGUAGE_TAGS.get(tag, tag[:2] if len(tag) == 2 else "")


def inventory(root: Path, recursive: bool = True, with_track_language: bool = True) -> dict:
    """Tree of the videos: [{path, name, folder, size, subtitles: [codes], track_language}], sorted by path."""
    root = Path(root)
    if not root.is_dir():
        raise source.SourceError(i18n.t("err.folder_not_found", path=root))
    videos = scan(root, recursive)
    if with_track_language:
        for v in videos:
            v["track_language"] = track_language(Path(v["path"]))
    return {"root": str(root).replace("\\", "/"), "videos": videos, "count": len(videos)}


def _natural_key(text: str) -> list:
    """Natural sort key: "Season 2" before "Season 10"."""
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", text)]


def scan(root: Path, recursive: bool = True, cancelled=lambda: False, progress=lambda _n: None) -> list[dict]:
    """Videos of the folder and subtitles next to them, without opening the files (fast); `track_language` is left empty."""
    root = Path(root)
    videos = []
    for current, subdirs, names in os.walk(root):
        if cancelled():
            break
        subdirs[:] = [s for s in subdirs if s != WORK_DIR] if recursive else []
        here = Path(current)
        rel = here.relative_to(root)
        for name in names:
            p = here / name
            if p.suffix.lower() not in VIDEO_EXTENSIONS or name.lower().endswith(MUXED_SUFFIX):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            neighbours = sorted({c for f in names if (c := subtitle_language_code(p.stem, Path(f))) is not None})
            videos.append({"path": str(p).replace("\\", "/"), "name": name, "folder": "" if rel == Path(".") else str(rel).replace("\\", "/"),
                           "size": size, "subtitles": neighbours, "track_language": ""})
        progress(len(videos))
    videos.sort(key=lambda v: (_natural_key(v["folder"]), _natural_key(v["name"])))
    return videos


class FolderScan:
    """Inventory in the background: fast scan (the list shows at once), then reading of the language of the audio
    tracks, which opens each video. Can be cancelled at any time; cancelling during the languages keeps the list."""

    def __init__(self, root: Path, recursive: bool = True, workers: int = 4):
        self.id = uuid.uuid4().hex[:8]
        self.root = Path(root)
        self.recursive = recursive
        self.workers = workers
        self.state = ScanState.SCANNING
        self.seen = 0
        self.done = 0
        self.videos: list[dict] | None = None
        self.languages: dict[str, str] = {}
        self.error = ""
        self._cancelled = threading.Event()
        self._lock = threading.Lock()
        batch_file = self.root / BATCH_FILE_NAME
        self.existing_batch = str(batch_file).replace("\\", "/") if batch_file.is_file() else None

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="folder-scan").start()

    def cancel(self):
        self._cancelled.set()

    def _read_language(self, v: dict):
        if self._cancelled.is_set():
            return
        code = track_language(Path(v["path"]))
        with self._lock:
            if code:
                self.languages[v["path"]] = code
            self.done += 1

    def run(self):
        try:
            videos = scan(self.root, self.recursive, self._cancelled.is_set, lambda n: setattr(self, "seen", n))
            if self._cancelled.is_set():
                self.state = ScanState.CANCELLED
                return
            self.seen, self.videos, self.state = len(videos), videos, ScanState.LANGUAGES
            with ThreadPoolExecutor(self.workers) as pool:
                list(pool.map(self._read_language, videos))
            self.state = ScanState.DONE
        except OSError as e:
            self.state, self.error = ScanState.ERROR, str(e)

    def to_dict(self, with_videos: bool = False) -> dict:
        with self._lock:
            languages = dict(self.languages)
            done = self.done
        d = {"id": self.id, "root": str(self.root).replace("\\", "/"), "state": self.state, "seen": self.seen, "done": done,
             "count": len(self.videos) if self.videos is not None else 0, "languages": languages, "error": self.error,
             "languages_interrupted": self._cancelled.is_set() and self.videos is not None, "existing_batch": self.existing_batch}
        if with_videos and self.videos is not None:
            d["videos"] = self.videos
        return d


# --- Batch file: selection, per-video settings, progress; read back to resume ------------------------------------

def batch_path(root: Path, output_dir: Path | None, in_output_dir: bool) -> Path:
    """In the output folder if the "output folder" destination is ticked, otherwise in the source folder."""
    return (Path(output_dir) if in_output_dir and output_dir else Path(root)) / BATCH_FILE_NAME


def write_batch(file: Path, batch: dict):
    file.parent.mkdir(parents=True, exist_ok=True)
    batch = {**batch, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(batch, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(file)


def read_batch(file: Path) -> dict:
    file = Path(file)
    if file.is_dir():
        file = file / BATCH_FILE_NAME
    if not file.is_file():
        raise source.SourceError(i18n.t("err.batch_file_not_found", path=file))
    try:
        batch = json.loads(file.read_text(encoding="utf-8"))
    except ValueError as e:
        raise source.SourceError(i18n.t("err.batch_file_unreadable", error=e)) from e
    if not isinstance(batch.get("videos"), list):
        raise source.SourceError(i18n.t("err.batch_file_no_videos"))
    batch["file"] = str(file).replace("\\", "/")
    return batch
