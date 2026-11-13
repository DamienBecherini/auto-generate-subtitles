"""Steps of a video: Transcription → AI review → Validation → Translation → Dubbing → MKV (mux).

What is done is read from the disk, to survive a restart of the service: a finished step is never redone.
`STOPS`: how far a batch goes by itself.
"""
import json
from pathlib import Path

from . import review

ORDER = ("transcription", "review", "validation", "translation", "dubbing", "mux")
STOPS = ("validation", "translation", "dubbing", "mux")
MKV_SUFFIX = ".multi.mkv"


def goes_to(stop: str | None, step: str) -> bool:
    """Is the step part of the requested work? Without a stop, the chain ends at translation."""
    return ORDER.index(step) <= ORDER.index(stop or "translation")


def mkv_path(video: Path) -> Path:
    return Path(video).with_name(Path(video).stem + MKV_SUFFIX)


def dubbing_file(work: Path, target: str) -> Path:
    """In a subfolder: cleaning the working folder deletes the top-level .m4a files (extracted audio)."""
    return Path(work) / "dubbing" / f"{target}.m4a"


def integration_mark(work: Path) -> Path:
    """Present when the MKV replaced the original video: no .multi.mkv next to it any more, but the dubbing is done."""
    return Path(work) / "dubbing" / "integrated.txt"


def _translation_kept(work: Path, video: Path, target: str) -> bool:
    return (work / f"translation.{target}.json").is_file() or video.with_name(f"{video.stem}.{target}.srt").is_file()


def observe(work: Path, video: str | Path, languages: list[str], spoken: str, pending: set[str]) -> dict:
    """Steps done for a video. `pending`: names still to validate in the batch (see review.pending_names)."""
    work, video = Path(work), Path(video)
    targets = [c for c in languages if c != spoken]
    reviewed_file = work / review.VIDEO_FILE
    reviewed, validated = False, False
    try:
        names = json.loads(reviewed_file.read_text(encoding="utf-8")).get("names", [])
        reviewed, validated = True, not pending.intersection(names)
    except (OSError, ValueError, AttributeError):
        pass
    muxed = mkv_path(video).is_file() or integration_mark(work).is_file()
    return {"transcribed": (work / "transcription.json").is_file(), "reviewed": reviewed, "validated": validated,
            "translated": bool(targets) and all(_translation_kept(work, video, c) for c in targets),
            "dubbed": muxed or (bool(targets) and dubbing_file(work, targets[0]).is_file()), "muxed": muxed}
