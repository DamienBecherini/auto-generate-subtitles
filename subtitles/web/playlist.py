"""Download of a playlist in the background, followed by the page: the videos go one by one into a folder of the
output folder named after the playlist; the page then opens this folder like any source folder (selection, language
per video, batch). Can be cancelled between two videos; running it again resumes (videos already there are kept)."""
import subprocess
import threading
import uuid
from pathlib import Path

from .. import i18n, source
from ..states import DownloadState


class PlaylistDownload:
    def __init__(self, url: str, command: list[str], output_dir: Path):
        self.id = uuid.uuid4().hex[:8]
        self.url = url
        self.command = command
        self.output_dir = Path(output_dir)
        self.state = DownloadState.LISTING
        self.title = ""
        self.folder: Path | None = None
        self.total = 0
        self.done = 0
        self.current = ""                      # title of the video being downloaded
        self.failures: list[dict] = []
        self.error = ""
        self.log: list[str] = []
        self._cancelled = threading.Event()

    def start(self):
        threading.Thread(target=self.run, daemon=True, name="playlist").start()

    def cancel(self):
        self._cancelled.set()

    def working(self) -> bool:
        return self.state in (DownloadState.LISTING, DownloadState.DOWNLOADING)

    def _progress(self, done: int, total: int, title: str):
        self.done, self.total, self.current = done, total, title

    def run(self):
        try:
            info = source.playlist_info(self.url, self.command)
            if info is None:
                raise source.SourceError(i18n.t("err.not_a_playlist"))
            self.title, self.total = info["title"], len(info["entries"])
            self.folder = source.playlist_folder(self.output_dir, info)
            if self._cancelled.is_set():
                self.state = DownloadState.CANCELLED
                return
            self.state = DownloadState.DOWNLOADING
            _files, self.failures = source.download_playlist(info, self.command, self.folder, self.log.append,
                                                             self._cancelled.is_set, self._progress)
            self.state = DownloadState.CANCELLED if self._cancelled.is_set() else DownloadState.DONE
        except (source.SourceError, OSError, subprocess.SubprocessError, ValueError) as e:
            self.state, self.error = DownloadState.ERROR, str(e)

    def to_dict(self) -> dict:
        return {"id": self.id, "url": self.url, "state": self.state, "title": self.title, "total": self.total, "done": self.done,
                "current": self.current, "folder": str(self.folder).replace("\\", "/") if self.folder else "",
                "failures": self.failures, "error": self.error}
