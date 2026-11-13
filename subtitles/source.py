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


def _output_options(command: list[str], folder: Path | None) -> list[str]:
    """Replaces the -o of the yt-dlp config by the requested output folder (name = title, final extension)."""
    if folder is None:
        return []
    return ["-o", remote_path(folder, command) + "/%(title)s.%(ext)s"]


def youtube_info(url: str, command: list[str], folder: Path | None = None) -> dict:
    """Id, title, duration (seconds) and target file, without downloading."""
    r = subprocess.run([*_command(command), "--no-playlist", "--no-download", *_output_options(command, folder),
                        "--print", "%(.{id,title,duration,filename})j", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise SourceError(f"yt-dlp : {(r.stderr or r.stdout).strip()[-500:]}")
    info = json.loads(r.stdout.strip().splitlines()[-1])
    info["video"] = local_path(info["filename"], command)
    return info


def download_youtube(url: str, command: list[str], log=print, folder: Path | None = None) -> Path:
    """Downloads the video with yt-dlp (in WSL by default): format and MP4 merge come from the yt-dlp config
    (~/.config/yt-dlp/config); the output folder replaces its own if `folder` is given. A video already there is
    not downloaded again. The audio is then extracted from the MP4 by ffmpeg, as for a file."""
    info = youtube_info(url, command, folder)
    video = info["video"]
    if video.exists():
        log(i18n.t("log.already_downloaded", path=video))
        return video
    log(i18n.t("log.downloading", title=info.get("title"), s=info.get("duration") or "?", path=video))
    r = subprocess.run([*_command(command), "--no-playlist", *_output_options(command, folder), url])
    if r.returncode != 0:
        raise SourceError(i18n.t("err.ytdlp_failed", code=r.returncode))
    if not video.exists():
        raise SourceError(i18n.t("err.download_missing", path=video))
    return video
