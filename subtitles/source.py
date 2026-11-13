"""Getting the video and its audio: YouTube address (yt-dlp) or file on the disk (ffmpeg).

Paths always with forward slashes (pathlib): the same code runs on Windows and in WSL.
"""
import json
import os
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from . import i18n


class SourceError(Exception):
    pass


AUDIO_EXTENSIONS = {".m4a", ".opus", ".mp3", ".ogg", ".flac", ".wav"}


def is_extracted_audio(p: Path, base: str) -> bool:
    """Audio file of a working folder: <base>.audio.<ext>, or <base>.<ext> (name used by the first versions)."""
    return p.suffix.lower() in AUDIO_EXTENSIONS and (p.name.startswith(base + ".audio.") or p.stem == base)


def is_url(s: str) -> bool:
    return re.match(r"https?://", s) is not None


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise SourceError(i18n.t("err.not_in_path", name=name))
    return path


def slug(title: str, max_len: int = 80) -> str:
    """Safe folder and file name: letters without accents, digits, dots and hyphens."""
    no_accents = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9.]+", "-", no_accents).strip("-.")
    s = re.sub(r"-{2,}", "-", s)
    return s[:max_len].rstrip("-.") or "video"


def _ffprobe(file: Path) -> dict:
    r = subprocess.run([tool("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                        str(file)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SourceError(f"ffprobe : {r.stderr.strip()[-300:]}")
    return json.loads(r.stdout)


def stream_types(file: Path) -> set[str]:
    return {s.get("codec_type") for s in _ffprobe(file).get("streams", [])}


def duration(file: Path) -> float:
    try:
        return float(_ffprobe(file)["format"]["duration"])
    except (KeyError, ValueError):
        return 0.0


# Container that accepts the codec as it is (copy without re-encoding); otherwise re-encoding to AAC.
_CONTAINERS = {"aac": "m4a", "opus": "opus", "mp3": "mp3", "vorbis": "ogg", "flac": "flac"}


def extract_audio(video: Path, target_without_extension: Path) -> Path:
    """Audio track of the video in a separate file (stream copy when the container allows it)."""
    streams = [s for s in _ffprobe(video).get("streams", []) if s.get("codec_type") == "audio"]
    if not streams:
        raise SourceError(i18n.t("err.no_audio_track", path=video))
    codec = streams[0].get("codec_name", "")
    ext = _CONTAINERS.get(codec)
    # Not with_suffix: "name.audio" would lose its ".audio" (the file must be called <name>.audio.<ext>)
    target = target_without_extension.parent / f"{target_without_extension.name}.{ext or 'm4a'}"
    codec_args = ["-c:a", "copy"] if ext else ["-c:a", "aac", "-b:a", "128k"]
    r = subprocess.run([tool("ffmpeg"), "-y", "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(video),
                        "-vn", "-map", "0:a:0", *codec_args, str(target)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0 or not target.exists():
        raise SourceError(f"ffmpeg : {r.stderr.strip()[-300:]}")
    return target


def _command(command: list[str]) -> list[str]:
    """Checks the first word of the command (wsl or yt-dlp) in the PATH."""
    return [tool(command[0]), *command[1:]]


def local_path(path: str, command: list[str]) -> Path:
    """A path given by yt-dlp in WSL (/mnt/d/…) becomes a Windows path (D:/…) when the command goes through wsl."""
    if os.name == "nt" and Path(command[0]).stem.lower() == "wsl":
        # Symbolic links (~/ytdl → /mnt/d/…) are resolved in WSL; the file may not exist yet.
        r = subprocess.run([command[0], "-e", "readlink", "-m", path], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if r.returncode == 0 and r.stdout.strip():
            path = r.stdout.strip()
        m = re.match(r"^/mnt/([a-zA-Z])/(.*)$", path)
        if not m:
            raise SourceError(i18n.t("err.ytdlp_outside_windows", path=path))
        return Path(f"{m.group(1).upper()}:/{m.group(2)}")
    return Path(path)


def remote_path(path: Path, command: list[str]) -> str:
    """Windows path → path seen by yt-dlp in WSL (D:/x → /mnt/d/x) when the command goes through wsl."""
    s = path.as_posix()
    if os.name == "nt" and Path(command[0]).stem.lower() == "wsl":
        m = re.match(r"^([a-zA-Z]):/(.*)$", s)
        if not m:
            raise SourceError(i18n.t("err.output_outside_windows", path=path))
        return f"/mnt/{m.group(1).lower()}/{m.group(2)}"
    return s


TITLE = "%(title)s"                            # yt-dlp template of a file name, without the extension


def ytdlp_format(max_height: int = 0, max_audio_kbps: int = 0) -> str:
    """yt-dlp -f: best image up to `max_height` with sound up to `max_audio_kbps`, falling back step by step down to
    the best file there is (a site without these sizes still gives its video)."""
    image = f"bv*[height<={max_height}]" if max_height else "bv*"
    sound = f"ba[abr<={max_audio_kbps}]" if max_audio_kbps else "ba"
    whole = f"b[height<={max_height}]" if max_height else "b"
    choices = [f"{image}+{sound}", f"{image}+ba", whole, "bv*+ba", "b"]
    return "/".join(dict.fromkeys(choices))


def ytdlp_command(cfg: dict) -> list[str]:
    """Configured yt-dlp command followed by the download settings ([youtube] table)."""
    y = cfg.get("youtube") or {}
    options = ["-f", ytdlp_format(int(y.get("max_height") or 0), int(y.get("max_audio_kbps") or 0))]
    if y.get("merge_format"):
        options += ["--merge-output-format", str(y["merge_format"])]
    if int(y.get("retries") or 0):
        options += ["--retries", str(int(y["retries"])), "--fragment-retries", str(int(y["retries"]))]
    return [*(y.get("command") or ["yt-dlp"]), *options, *[str(a) for a in y.get("extra_args") or []]]


def download_dir(cfg: dict, output_dir: str | Path | None = None) -> Path:
    """Folder of the downloaded videos: `download_dir` of [youtube], otherwise the output folder."""
    return Path(os.path.expanduser((cfg.get("youtube") or {}).get("download_dir") or output_dir or cfg["output_dir"]))


def _output_options(command: list[str], folder: Path | None, name: str = TITLE) -> list[str]:
    """Replaces the -o of the yt-dlp config by the requested output folder (name = title, final extension)."""
    if folder is None:
        return []
    return ["-o", remote_path(folder, command) + f"/{name}.%(ext)s"]


def youtube_info(url: str, command: list[str], folder: Path | None = None, name: str = TITLE) -> dict:
    """Id, title, duration (seconds) and target file, without downloading. Any site yt-dlp knows, not only YouTube.
    The address of a whole playlist is refused: one line per video would come back, and the download would take them all."""
    r = subprocess.run([*_command(command), "--no-playlist", "--no-download", *_output_options(command, folder, name),
                        "--print", "%(.{id,title,duration,filename})j", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SourceError(f"yt-dlp : {(r.stderr or r.stdout).strip()[-500:]}")
    lines = r.stdout.strip().splitlines()
    if len(lines) > 1:
        raise SourceError(i18n.t("err.is_playlist", n=len(lines)))
    info = json.loads(lines[-1])
    info["video"] = local_path(info["filename"], command)
    return info


def download_youtube(url: str, command: list[str], log=print, folder: Path | None = None, name: str = TITLE) -> Path:
    """Downloads the video with yt-dlp (in WSL by default): format and MP4 merge come from the yt-dlp config
    (~/.config/yt-dlp/config); the output folder replaces its own if `folder` is given. A video already there is
    not downloaded again. The audio is then extracted from the MP4 by ffmpeg, as for a file."""
    info = youtube_info(url, command, folder, name)
    video = info["video"]
    if video.exists():
        log(i18n.t("log.already_downloaded", path=video))
        return video
    log(i18n.t("log.downloading", title=info.get("title"), s=info.get("duration") or "?", path=video))
    r = subprocess.run([*_command(command), "--no-playlist", *_output_options(command, folder, name), url])
    if r.returncode != 0:
        raise SourceError(i18n.t("err.ytdlp_failed", code=r.returncode))
    if not video.exists():
        raise SourceError(i18n.t("err.download_missing", path=video))
    return video


# --- Playlists: downloaded into a folder of their own, which then becomes a batch like any folder -----------------

PLAYLIST_TIMEOUT_S = 300


def _flat(url: str, command: list[str], *options: str) -> dict:
    r = subprocess.run([*_command(command), "--flat-playlist", *options, "-J", url], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=PLAYLIST_TIMEOUT_S)
    if r.returncode != 0:
        raise SourceError(f"yt-dlp : {(r.stderr or r.stdout).strip()[-500:]}")
    return json.loads(r.stdout)


def playlist_info(url: str, command: list[str]) -> dict | None:
    """{id, title, entries: [{url, title, duration}], with_video} if the address is a playlist (or a
    channel), None otherwise. Read without downloading anything. `with_video`: the address also designates
    one video of the list (youtube.com/watch?v=…&list=…), which can be processed alone."""
    d = _flat(url, command, "--yes-playlist")
    if d.get("_type") != "playlist":
        return None
    entries = [{"url": e.get("url") or e.get("webpage_url"), "title": e.get("title") or e.get("id") or "?", "duration": e.get("duration")}
               for e in d.get("entries") or [] if e and (e.get("url") or e.get("webpage_url"))]
    if not entries:
        return None
    alone = _flat(url, command, "--no-playlist")
    return {"id": d.get("id"), "title": d.get("title") or d.get("id") or "playlist", "entries": entries,
            "with_video": alone.get("_type") != "playlist"}


def folder_name(title: str, max_len: int = 100) -> str:
    """Readable folder name (accents and spaces kept), without the characters Windows refuses."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", title)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return s[:max_len].rstrip(" .") or "playlist"


def playlist_folder(output_dir: str | Path, info: dict) -> Path:
    return Path(output_dir) / folder_name(info["title"])


def download_playlist(info: dict, command: list[str], folder: Path, log=print, cancelled=lambda: False,
                      progress=lambda done, total, title: None) -> tuple[list[Path], list[dict]]:
    """Downloads the videos one by one into `folder`, numbered in the order of the list ("001 - title"). A video that
    fails (private, removed) is skipped; one already there is not downloaded again, so running it again resumes.
    Returns (files, failures [{title, url, error}])."""
    folder.mkdir(parents=True, exist_ok=True)
    entries = info["entries"]
    width = max(3, len(str(len(entries))))
    files, failures = [], []
    for i, e in enumerate(entries, 1):
        if cancelled():
            break
        progress(i - 1, len(entries), e["title"])
        log(i18n.t("log.playlist_video", i=i, n=len(entries), title=e["title"]))
        try:
            files.append(download_youtube(e["url"], command, log, folder, f"{i:0{width}d} - {TITLE}"))
        except (SourceError, OSError, subprocess.SubprocessError, ValueError) as err:
            log(i18n.t("log.playlist_video_failed", title=e["title"], error=err))
            failures.append({"title": e["title"], "url": e["url"], "error": str(err)})
    progress(len(files) + len(failures), len(entries), "")
    return files, failures
