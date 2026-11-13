"""Writing of the SRT and WebVTT subtitle files."""
from .segmentation import Cue, Settings, wrap_lines


def hms(t: float, separator: str = ",") -> str:
    t = max(0.0, t)
    ms = round(t * 1000)
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{separator}{ms:03d}"


def srt(cues: list[Cue], r: Settings | None = None) -> str:
    r = r or Settings()
    blocks = []
    for i, s in enumerate(cues, 1):
        blocks.append(f"{i}\n{hms(s.start)} --> {hms(s.end)}\n" + "\n".join(wrap_lines(s.text, r)) + "\n")
    return "\n".join(blocks)


def vtt(cues: list[Cue], r: Settings | None = None) -> str:
    r = r or Settings()
    blocks = ["WEBVTT\n"]
    for s in cues:
        blocks.append(f"{hms(s.start, '.')} --> {hms(s.end, '.')}\n" + "\n".join(wrap_lines(s.text, r)) + "\n")
    return "\n".join(blocks)
