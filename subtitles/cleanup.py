"""Cleanup of the working folders (`<output_dir>/.work/<name>/`) by category of files.

Categories (`[cleanup]` options of the configuration, `true` = delete):
  audio          `<name>.audio.*`                             the extracted track, the largest file (redone if needed, a few seconds)
  subtitles      `*.srt`, `*.vtt`                              the copies of the working folder (the useful files are next to the video)
  transcription  `transcription.json`, `translation*.json`     raw data: kept by default, they allow adding a language or
                                                              changing the layout without transcribing or translating again
  logs           `log-*.txt`                                   kept by default; those older than `logs_age_h` hours are purged
                                                              when the interface starts and by "Clean now"
"""
import time
from pathlib import Path

from . import i18n

CATEGORIES = {
    "audio": ["*.audio.*", "*.m4a", "*.opus", "*.mp3", "*.ogg", "*.flac", "*.wav"],   # bare extensions: name used by the first versions
    "subtitles": ["*.srt", "*.vtt"],
    "transcription": ["transcription.json", "translation*.json"],
    "logs": ["log-*.txt"],
}
DEFAULTS = {"audio": True, "subtitles": True, "transcription": False, "logs": False, "logs_age_h": 48}


def options(cfg: dict) -> dict:
    return {**DEFAULTS, **(cfg.get("cleanup") or {})}


def classify(work: Path) -> dict[str, list[Path]]:
    """Files of a working folder by category."""
    r = {}
    for cat, patterns in CATEGORIES.items():
        seen = []
        for pattern in patterns:
            seen.extend(p for p in work.glob(pattern) if p.is_file())
        r[cat] = sorted(set(seen))
    return r


def _delete(files: list[Path]) -> tuple[int, int]:
    count = size = 0
    for f in files:
        try:
            n = f.stat().st_size
            f.unlink()
            count += 1
            size += n
        except OSError:
            pass
    return count, size


def clean_folder(work: Path, opts: dict, log=lambda _m: None, keep: set[Path] | None = None,
                 old_logs_only: bool = True) -> tuple[int, int]:
    """Applies the options to a working folder. `keep`: files not to touch (only copy of a result).
    Logs are deleted only if they are older than `logs_age_h` hours (unless asked otherwise)."""
    if not work.is_dir():
        return 0, 0
    keep = {p.resolve() for p in (keep or set())}
    limit = _log_limit(float(opts.get("logs_age_h", 48)))
    total_count = total_bytes = 0
    for cat, files in classify(work).items():
        if not opts.get(cat):
            continue
        targets = [f for f in files if f.resolve() not in keep]
        if cat == "logs" and old_logs_only:
            targets = [f for f in targets if f.stat().st_mtime <= limit]
        count, size = _delete(targets)
        if count:
            log(i18n.t("log.cleanup", category=i18n.label("cleanup.", cat), n=count, mb=f"{size / 1e6:.1f}"))
        total_count += count
        total_bytes += size
    return total_count, total_bytes


def _log_limit(age_h: float) -> float:
    """Date before which a log is old enough to go. 0 hours: every log, even one just written (on Windows the date of
    a file can be a few milliseconds ahead of time.time())."""
    return float("inf") if age_h <= 0 else time.time() - age_h * 3600


def purge_logs(root: Path, age_h: float) -> tuple[int, int]:
    """Logs older than `age_h` hours in every working folder."""
    if not root.is_dir():
        return 0, 0
    limit = _log_limit(age_h)
    return _delete([f for f in root.glob("*/log-*.txt") if f.is_file() and f.stat().st_mtime <= limit])


def summary(root: Path) -> dict[str, dict]:
    """Number of files and bytes per category over every working folder."""
    r = {cat: {"files": 0, "bytes": 0} for cat in CATEGORIES}
    if root.is_dir():
        for d in root.iterdir():
            if d.is_dir():
                for cat, files in classify(d).items():
                    r[cat]["files"] += len(files)
                    r[cat]["bytes"] += sum(f.stat().st_size for f in files)
    return r


def clean_all(root: Path, opts: dict) -> dict[str, dict]:
    """"Clean now": applies the options to every working folder (logs: only the old ones), then deletes the empty
    folders. Returns {category: {files, bytes}} deleted."""
    r = {cat: {"files": 0, "bytes": 0} for cat in CATEGORIES}
    if not root.is_dir():
        return r
    for d in sorted(p for p in root.iterdir() if p.is_dir()):
        before = classify(d)
        sizes = {f: f.stat().st_size for files in before.values() for f in files}
        clean_folder(d, opts)
        after = classify(d)
        for cat in CATEGORIES:
            remaining = set(after[cat])
            gone = [f for f in before[cat] if f not in remaining]
            r[cat]["files"] += len(gone)
            r[cat]["bytes"] += sum(sizes[f] for f in gone)
        try:
            if not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass
    return r
