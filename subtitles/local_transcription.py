"""Transcription server started on this PC on demand ("transcriptions" entry with a `command`)."""
import atexit
import json
import os
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import i18n, transcription

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
STARTUP_FAILURE_CODE = 3                # uvicorn: listening on the port failed


class LocalServerError(Exception):
    pass


def _responds(url: str) -> bool:
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=2) as r:
            return transcription.health_state(json.loads(r.read() or b"{}")) == "ready"
    except (OSError, ValueError):
        return False


def port_owner(url: str) -> str:
    """Program listening on the port of `url`: "name (pid n)", or "" if there is none or outside Windows."""
    if os.name != "nt":
        return ""
    port = urllib.parse.urlsplit(url).port
    try:
        lines = subprocess.run(["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, errors="replace", timeout=20,
                               creationflags=_NO_WINDOW).stdout.splitlines()
        pid = next((p[4] for p in map(str.split, lines) if len(p) == 5 and p[1].endswith(f":{port}") and p[3] == "LISTENING"), None)
        if not pid:
            return ""
        name = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True, text=True, errors="replace", timeout=20,
                              creationflags=_NO_WINDOW).stdout.split(",")[0].strip().strip('"')
        return f"{name or i18n.t('state.unknown_program')} (pid {pid})"
    except (OSError, subprocess.SubprocessError):
        return ""


class LocalServer:
    """A single process per URL; `ensure()` starts it if it does not answer, `stop()` stops it if it was started here."""

    _instances: dict[str, "LocalServer"] = {}
    _lock = threading.Lock()

    def __init__(self, m: dict):
        self.url = m["url"].rstrip("/")
        self.command = m["command"]
        self.cwd = m.get("cwd") or None
        self.process: subprocess.Popen | None = None
        self.log_file = Path(os.environ.get("TEMP", ".")) / "local-transcription.log"

    @classmethod
    def for_engine(cls, m: dict) -> "LocalServer":
        with cls._lock:
            if m["url"] not in cls._instances:
                cls._instances[m["url"]] = cls(m)
            return cls._instances[m["url"]]

    def running_here(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def state(self) -> str:
        if _responds(self.url):
            return "ready"
        return "starting" if self.running_here() else "stopped"

    def ensure(self, log=print, wait_s: int = 180) -> bool:
        """Returns True if it had to be started."""
        if _responds(self.url):
            return False
        if not self.running_here():
            log(i18n.t("log.local_server_starting", url=self.url))
            out = open(self.log_file, "ab")
            self.process = subprocess.Popen(self.command, cwd=self.cwd, stdout=out, stderr=subprocess.STDOUT,
                                            creationflags=_NO_WINDOW)
            out.close()
            atexit.register(self.stop)
        start = time.time()
        while time.time() - start < wait_s:
            if _responds(self.url):
                log(i18n.t("log.local_server_ready", s=round(time.time() - start)))
                return True
            if self.process and self.process.poll() is not None:
                code = self.process.returncode
                owner = port_owner(self.url) if code == STARTUP_FAILURE_CODE else ""
                detail = i18n.t("err.port_used_by", program=owner) if owner else ""
                raise LocalServerError(i18n.t("err.local_server_stopped", code=code, detail=detail, log=self.log_file))
            time.sleep(1)
        raise LocalServerError(i18n.t("err.local_server_timeout"))

    def stop(self):
        if self.running_here():
            p = self.process
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=20,
                                   creationflags=_NO_WINDOW)
                else:
                    p.terminate()
                p.wait(timeout=10)
            except (subprocess.SubprocessError, OSError):
                pass
        self.process = None
