"""Creates a `<name>.multi.mkv` next to the video: video and original tracks copied without re-encoding, dubbed track
and subtitles added.

The original video is never modified. The file appears under its final name only once checked.
"""
import json
import os
import shutil
import subprocess
from pathlib import Path

from . import folder, i18n, source, steps

_CODES3: dict[str, str] = {}
for _three, _two in folder.LANGUAGE_TAGS.items():
    _CODES3.setdefault(_two, _three)                   # the first form is the bibliographic one (fre, ger), the one of Matroska
_TEXT_TO_CONVERT = {"mov_text"}                        # subtitles of an .mp4: Matroska cannot copy them as they are
MAX_GAP_S = 2.0


class MuxError(source.SourceError):
    pass


def code3(code: str) -> str:
    return _CODES3.get(code, "und")


def title(code: str) -> str:
    return i18n.language_name(code)[:1].upper() + i18n.language_name(code)[1:]


def probe(path: Path) -> dict:
    r = subprocess.run([source.tool("ffprobe"), "-v", "error", "-show_entries", "stream=index,codec_type,codec_name:stream_tags=language,title:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode:
        raise MuxError(i18n.t("err.probe_failed", file=Path(path).name, detail=r.stderr[:300]))
    return json.loads(r.stdout)


def command(ffmpeg: str, video: Path, info: dict, dubbed: Path, target: str, spoken: str, subtitles: list[tuple[str, Path]], output: Path) -> list[str]:
    """ffmpeg options. `subtitles`: [(code, .srt file)]; `info`: result of `probe(video)`."""
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    existing = [s for s in info["streams"] if s["codec_type"] == "subtitle"]
    n_a, n_s = len(audios), len(existing)
    cmd = [ffmpeg, "-loglevel", "error", "-y", "-i", str(video), "-i", str(dubbed)]
    for _, f in subtitles:
        cmd += ["-i", str(f)]
    cmd += ["-map", "0:v", "-map", "0:a", "-map", "0:s?", "-map", "1:a:0"]
    for i in range(len(subtitles)):
        cmd += ["-map", f"{2 + i}:0"]
    cmd += ["-c:v", "copy", "-c:a", "copy", "-c:s", "copy"]
    for i, s in enumerate(existing):
        if s.get("codec_name") in _TEXT_TO_CONVERT:
            cmd += [f"-c:s:{i}", "srt"]
    for i in range(len(subtitles)):
        cmd += [f"-c:s:{n_s + i}", "srt"]
    for i, a in enumerate(audios):
        tags = a.get("tags") or {}
        if tags.get("language", "und") == "und":
            cmd += [f"-metadata:s:a:{i}", f"language={code3(spoken)}"]
        if not tags.get("title"):
            cmd += [f"-metadata:s:a:{i}", f"title={title(spoken)} (original)"]
        cmd += [f"-disposition:a:{i}", "default" if i == 0 else "0"]
    cmd += [f"-metadata:s:a:{n_a}", f"language={code3(target)}", f"-metadata:s:a:{n_a}", f"title={i18n.t('mkv.dub_track', lang=title(target))}", f"-disposition:a:{n_a}", "0"]
    for i, (code, _) in enumerate(subtitles):
        cmd += [f"-metadata:s:s:{n_s + i}", f"language={code3(code)}", f"-metadata:s:s:{n_s + i}", f"title={title(code)}", f"-disposition:s:{n_s + i}", "0"]
    return cmd + ["-f", "matroska", str(output)]


def verify(mkv: Path, info: dict, added: int):
    """The MKV must hold at least the original tracks plus the added ones, and last as long as the video."""
    done, expected = probe(mkv), len(info["streams"]) + added
    gap = abs(float(done["format"]["duration"]) - float(info["format"]["duration"]))
    if len(done["streams"]) != expected or gap > MAX_GAP_S:
        raise MuxError(i18n.t("err.mkv_incomplete", found=len(done["streams"]), expected=expected, gap=f"{gap:.1f}"))


def to_trash(file: Path) -> bool:
    """Sends a file to the recycle bin; False if it is not possible (the file then stays in place)."""
    file = Path(file)
    try:
        if os.name == "nt":                    # path passed through the environment: no quotes or accents to escape
            script = ("Add-Type -AssemblyName Microsoft.VisualBasic; [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile("
                      "$env:TRASH_FILE, 'OnlyErrorDialogs', 'SendToRecycleBin')")
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], env={**os.environ, "TRASH_FILE": str(file)},
                           capture_output=True, timeout=600, check=True)
        elif shutil.which("gio"):
            subprocess.run(["gio", "trash", str(file)], capture_output=True, timeout=600, check=True)
        else:
            return False
    except (OSError, subprocess.SubprocessError):
        return False
    return not file.exists()


def replace_original(video: Path, mkv: Path, added: int, log=print) -> Path:
    """Replaces the video by the MKV, only if it is checked and if the original did go to the recycle bin. Returns the
    final file. The subtitles next to it keep the same base name: only the extension of the video can change."""
    video, mkv = Path(video), Path(mkv)
    final = video.with_suffix(".mkv")
    if final != video and final.exists():
        log(i18n.t("log.replace_exists", file=final.name))
        return mkv
    verify(mkv, probe(video), added)
    if not to_trash(video):
        log(i18n.t("log.trash_unavailable"))
        return mkv
    mkv.replace(final)
    log(i18n.t("log.replaced", file=final.name))
    return final


def mux(video: Path, dubbed: Path, target: str, spoken: str, subtitles: list[tuple[str, Path]], log=print, redo: bool = False) -> Path:
    video = Path(video)
    output = steps.mkv_path(video)
    if output.exists() and not redo:
        log(i18n.t("log.mkv_already", file=output.name))
        return output
    partial = output.with_name(output.name + ".part")
    info = probe(video)
    try:
        r = subprocess.run(command(source.tool("ffmpeg"), video, info, dubbed, target, spoken, subtitles, partial), capture_output=True, text=True, encoding="utf-8", errors="replace")
        if r.returncode:
            raise MuxError(f"ffmpeg : {r.stderr[-400:]}")
        verify(partial, info, 1 + len(subtitles))
        partial.replace(output)
    finally:
        partial.unlink(missing_ok=True)
    log(i18n.t("log.mkv_done", path=output, mb=f"{output.stat().st_size / 2**20:.0f}"))
    return output
