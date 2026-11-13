"""Time left of a piece of work: observed speed, remembered from one session to the next, otherwise the speed given
in the configuration.

A "pass" is one passage of a video through the queue: 1 = preparation (with the translation if the batch does not
pause), 2 = translation after a pause, 3 = dubbing and mux. Its weight says what makes its duration vary: seconds of
video in passes 1 and 3, lines × languages in pass 2. The rate is the number of seconds of computing per unit of weight.
"""
import json
import re
import threading

from .. import config, estimation, i18n

FILE = config.user_dir() / "speeds.json"
MIN_DURATION_S = 5             # below, the pass reused results already done: it says nothing about the speed
LINES_PER_SECOND = 0.29        # subtitles per second of video (measured: 494 and 562 lines for 30 min)
DUBBING_PER_SECOND = 0.28      # seconds of graphics card per second of video (measured: 8 min 30 for 30 min)

_lock = threading.Lock()
_memory: dict | None = None


def key(pass_no: int, options: dict) -> str:
    """The speed depends on what works: transcription server, translator, or both one after the other."""
    transcription, translator = options.get("transcription") or "", options.get("translator") or ""
    if pass_no == 3:
        return "dubbing"
    if pass_no == 2:
        return f"translation|{translator}"
    return f"full|{transcription}|{translator}" if options.get("pause", True) is False else f"transcription|{transcription}"


def _load() -> dict:
    global _memory
    if _memory is None:
        try:
            _memory = json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _memory = {}
    return _memory


def read(k: str) -> float | None:
    with _lock:
        return _load().get(k)


def record(k: str, rate: float):
    """Average with the remembered value: the memory follows machine changes without depending on a single video."""
    with _lock:
        m = _load()
        m[k] = (m[k] + rate) / 2 if k in m else rate
        try:
            FILE.parent.mkdir(parents=True, exist_ok=True)
            FILE.write_text(json.dumps(m, indent=1), encoding="utf-8")
        except OSError:
            pass                       # without memory, the speed will be learnt again


def _announced_rate(pass_no: int, options: dict) -> float | None:
    """As long as nothing was measured: speed of the transcription server and time per line from the configuration."""
    if pass_no == 3:
        return DUBBING_PER_SECOND
    try:
        cfg = config.load()
        per_line = float(estimation.settings(cfg)["local_seconds_per_line"])
        translator = config.engine(cfg, "translators", "active_translator", options.get("translator") or None)
        with_llm = translator.get("type", "openai") == "openai"
        if pass_no == 2:
            return per_line if with_llm else None
        server = config.engine(cfg, "transcriptions", "active_transcription", options.get("transcription") or None)
        speed = float(server.get("speed") or 0)
        if speed <= 0:
            return None
        rate = 1 / speed
        if options.get("pause", True) is False and with_llm:
            rate += LINES_PER_SECOND * per_line * max(1, len(options.get("languages") or []))
        return rate
    except (config.ConfigError, OSError, ValueError, KeyError, TypeError):
        return None


def known_rate(pass_no: int, options: dict) -> float | None:
    return read(key(pass_no, options)) or _announced_rate(pass_no, options)


_TIME = re.compile(r"^\[(\d\d):(\d\d):(\d\d)\] ")


def log_duration(lines: list[str]) -> int:
    """Duration of a task read in its log: from the first to the last timed line, without the time spent paused.
    The pause and resume marks are recognised in every language (a log keeps the one it was written in)."""
    pauses = tuple("] " + v for v in i18n.variants("log.pause_prefix"))
    resumes = tuple("] " + v for v in i18n.variants("log.resume_prefix"))
    total, previous, paused = 0, None, False
    for line in lines:
        m = _TIME.match(line)
        if not m:
            continue
        s = 3600 * int(m[1]) + 60 * int(m[2]) + int(m[3])
        if previous is not None and not paused:
            total += (s - previous) % 86400
        previous = s
        body = line[m.end() - 2:]
        if body.startswith(pauses):
            paused = True
        elif body.startswith(resumes):
            paused = False
    return total
