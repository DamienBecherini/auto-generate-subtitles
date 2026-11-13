"""AI dubbing: runs `dubbing_worker.py` in the separate environment that holds torch, demucs and OmniVoice.

`[dubbing]` table of the configuration: `enabled`, `command` (Python interpreter of that environment), `model`, `wsl`
(paths translated to /mnt/… when the command goes through WSL). The first line sent to the program is its job as
JSON; its input then stays open: closing it (cancellation, service shutdown) tells it to stop.
"""
import json
import re
import subprocess
from collections import deque
from pathlib import Path

from . import i18n, steps

WORKER = Path(__file__).with_name("dubbing_worker.py")
PREFIX = "» "                                   # lines of the program meant for the log


class DubbingError(Exception):
    pass


def available(cfg: dict) -> bool:
    d = cfg.get("dubbing") or {}
    return bool(d.get("enabled") and d.get("command") and d.get("model"))


def path_for(cfg: dict, p: str | Path) -> str:
    """Path as seen by the program: D:/x becomes /mnt/d/x behind WSL."""
    text = str(p).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", text)
    return f"/mnt/{m.group(1).lower()}/{m.group(2)}" if m and (cfg.get("dubbing") or {}).get("wsl") else text


def worker_message(raw: str) -> str:
    """Line of the dubbing program: {"key": …, parameters} translated into the active language; text as it is otherwise."""
    try:
        d = json.loads(raw)
    except ValueError:
        return raw
    if not isinstance(d, dict) or "key" not in d:
        return raw
    return i18n.t(d.pop("key"), **d)


def dub(cfg: dict, video: Path, work: Path, source_srt: Path, target_srt: Path, target: str, log=print, cancelled=lambda: False, redo: bool = False) -> Path:
    """Makes `dubbing/<target>.m4a` in the working folder (cloned voice mixed with the original background sound) and returns its path."""
    output = steps.dubbing_file(work, target)
    if output.is_file() and not redo:
        log(i18n.t("log.dub_already_done", lang=target, file=output.name))
        return output
    if not available(cfg):
        raise DubbingError(i18n.t("err.dub_not_configured"))
    output.parent.mkdir(parents=True, exist_ok=True)
    conf = cfg["dubbing"]
    c = lambda p: path_for(cfg, p)  # noqa: E731
    job = {"video": c(video), "source_srt": c(source_srt), "target_srt": c(target_srt), "language": target, "output": c(output),
           "temp_dir": c(output.parent / f"temp-{target}"), "model": conf["model"],
           "steps": int(conf.get("steps", 32))}
    try:
        p = subprocess.Popen([*conf["command"], c(WORKER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    except OSError as e:
        raise DubbingError(i18n.t("err.dub_program_not_found", error=e)) from e
    p.stdin.write((json.dumps(job, ensure_ascii=False) + "\n").encode("utf-8"))
    p.stdin.flush()
    tail, was_cancelled = deque(maxlen=15), False
    buffer = b""
    while True:
        block = p.stdout.read(4096)
        if not block:
            break
        buffer += block
        *lines, buffer = re.split(rb"[\r\n]+", buffer)
        for raw in lines:
            line = raw.decode("utf-8", "replace")
            if line.startswith(PREFIX):
                log(worker_message(line[len(PREFIX):]))
            elif line.strip():
                tail.append(line)
        if cancelled() and not was_cancelled:
            was_cancelled = True
            p.stdin.close()                     # the program stops as soon as its input closes
            p.terminate()
    code = p.wait()
    if was_cancelled:
        raise DubbingError(i18n.t("err.cancelled"))
    if code or not output.is_file():
        raise DubbingError(i18n.t("err.dub_failed", code=code, detail=" | ".join(list(tail)[-4:])[-500:]))
    return output
