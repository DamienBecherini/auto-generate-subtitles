"""Local HTTP server of the interface (standard library, like the supervision).

Each route is a function declared with @route("METHOD", "/api/path/{id}"), its body fields in its docstring;
Handler._dispatch finds it, turns the errors into JSON answers and sends the result.
"""
import shutil
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, NamedTuple
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__, cleanup, config, folder, i18n, language, local_transcription, source, steps, transcription, translation
from .idle import IdleWatch, is_activity
from .manager import Manager

log = logging.getLogger("subtitles.web")
STATIC = Path(__file__).resolve().parent / "static"
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".svg": "image/svg+xml", ".png": "image/png"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
manager = Manager()
_imposed_timeout: float | None = None   # --idle of the command line
current_scan: folder.FolderScan | None = None   # inventory of the folder in progress or displayed (one at a time)


def idle_timeout_s() -> float:
    if _imposed_timeout is not None:
        return _imposed_timeout
    try:
        return float(config.load().get("web", {}).get("idle_min", 60)) * 60
    except (config.ConfigError, OSError, ValueError):
        return 0                        # unreadable configuration: do not stop


idle = IdleWatch(idle_timeout_s, manager.busy)


def health() -> dict:
    """Answer of GET /api/health, following the "local service" contract v2."""
    remaining = idle.remaining_s()
    return {"state": "ready", "contract": 2, "service": "subtitles", "version": __version__,
            "busy": manager.busy(), "activity": manager.activity(), "remaining_work_s": manager.remaining_s(),
            "idle": {"timeout_s": idle_timeout_s(), "remaining_s": None if remaining is None else round(remaining, 1)}}


class RequestError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def local_ai_state(cfg: dict) -> dict | None:
    for m in cfg.get("translators", []):
        if m.get("supervision"):
            try:
                with urllib.request.urlopen(m["supervision"].rstrip("/") + "/api/ai", timeout=5) as r:   # nvidia-smi ≈ 1 s
                    d = json.load(r)
                return {"state": d.get("state"), "models": [x.get("id") for x in d.get("models", [])]}
            except (urllib.error.URLError, OSError, ValueError):
                return {"state": "unknown"}
    return None


def local_transcription_state(cfg: dict) -> dict:
    r = {}
    for m in cfg.get("transcriptions", []):
        if m.get("command"):
            r[m["id"]] = local_transcription.LocalServer.for_engine(m).state()
    return r


_TITLES = {"file": "dialog.pick_video", "batch": "dialog.pick_batch", "source_folder": "dialog.source_folder"}
_VIDEOS = "*.mp4 *.mkv *.webm *.mov *.avi *.m4a *.mp3 *.opus *.ogg *.wav *.flac"
_PATTERNS = {"file": (_VIDEOS, "dialog.videos_audio"), "batch": ("*.batch.json", "dialog.batch_file")}
_MAC_SCRIPT = ("on run argv\n"
               "if (count of argv) > 1 then return POSIX path of (choose {what} with prompt (item 1 of argv) "
               "default location ((POSIX file (item 2 of argv)) as alias))\n"
               "return POSIX path of (choose {what} with prompt (item 1 of argv))\n"
               "end run")


def dialog_command(kind: str, start: str = "", platform: str = sys.platform, find=shutil.which) -> list[str] | None:
    """Dialog box outside Windows: osascript on macOS, zenity or kdialog elsewhere; None if none is installed."""
    title, pattern = i18n.t(_TITLES.get(kind, "dialog.output_folder")), _PATTERNS.get(kind)
    if pattern:
        pattern = (pattern[0], i18n.t(pattern[1]))
    if platform == "darwin":
        script = _MAC_SCRIPT.format(what="file" if pattern else "folder")
        return ["osascript", "-e", script, title] + ([start] if start else [])
    if find("zenity"):
        c = ["zenity", "--file-selection", f"--title={title}"]
        if start:
            c.append(f"--filename={start.rstrip('/')}/")
        if pattern:
            return c + [f"--file-filter={pattern[1]} | {pattern[0]}", f"--file-filter={i18n.t('dialog.all_files')} | *"]
        return c + ["--directory"]
    if find("kdialog"):
        if pattern:
            return ["kdialog", "--title", title, "--getopenfilename", start or ".", f"{pattern[0]}|{pattern[1]}"]
        return ["kdialog", "--title", title, "--getexistingdirectory", start or "."]
    return None


def _unix_dialog(kind: str, start: str) -> str:
    command = dialog_command(kind, start)
    if command is None:
        raise RequestError(501, i18n.t("err.no_dialog"))
    r = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    path = r.stdout.strip() if r.returncode == 0 else ""     # non-zero code: choice cancelled
    return path.rstrip("/") or path


def dialog(kind: str, start: str = "") -> str:
    """System dialog box, in the foreground (Windows: PowerShell in STA mode); empty string if cancelled."""
    if os.name != "nt":
        return _unix_dialog(kind, start)
    start = start.replace("'", "''")
    if kind in ("file", "batch"):
        every = i18n.t("dialog.all_files")
        filt = (f"{i18n.t('dialog.batch_file')}|*.batch.json|{every}|*.*" if kind == "batch" else
                f"{i18n.t('dialog.videos_audio')}|*.mp4;*.mkv;*.webm;*.mov;*.avi;*.m4a;*.mp3;*.opus;*.ogg;*.wav;*.flac|{every}|*.*")
        filt = filt.replace("'", "''")
        body = ("$d = New-Object System.Windows.Forms.OpenFileDialog; "
                f"$d.Filter = '{filt}'; "
                f"if ('{start}') {{ $d.InitialDirectory = '{start}' }}; "
                "if ($d.ShowDialog($f) -eq 'OK') { $d.FileName }")
    else:
        title = i18n.t("dialog.source_folder" if kind == "source_folder" else "dialog.output_folder").replace("'", "''")
        body = (f"$d = New-Object System.Windows.Forms.FolderBrowserDialog; $d.Description = '{title}'; "
                f"if ('{start}') {{ $d.SelectedPath = '{start}' }}; "
                "if ($d.ShowDialog($f) -eq 'OK') { $d.SelectedPath }")
    script = ("[Console]::OutputEncoding = [Text.Encoding]::UTF8; Add-Type -AssemblyName System.Windows.Forms; "
              "$f = New-Object System.Windows.Forms.Form; $f.TopMost = $true; $f.StartPosition = 'CenterScreen'; " + body)
    r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600, creationflags=_NO_WINDOW)
    return r.stdout.strip().replace("\\", "/")


def open_command(p: Path, platform: str = sys.platform) -> list[str]:
    """Shows a folder, or a file in its folder (selected when the system allows it)."""
    if platform == "win32":
        return ["explorer", str(p)] if p.is_dir() else ["explorer", "/select,", str(p)]
    if platform == "darwin":
        return ["open", str(p)] if p.is_dir() else ["open", "-R", str(p)]
    return ["xdg-open", str(p if p.is_dir() else p.parent)]


def open_path(path: str):
    p = Path(path)
    if not p.exists():
        raise RequestError(404, i18n.t("err.not_found_path", path=path))
    try:
        subprocess.Popen(open_command(p))
    except OSError as e:
        raise RequestError(501, i18n.t("err.cannot_open", error=e)) from e


def test_engine(kind: str, m: dict) -> dict:
    if kind == "transcription":
        if m.get("command"):
            local_transcription.LocalServer.for_engine(m).ensure(lambda _x: None)
        return {"ok": True, "detail": i18n.t("test.responds", state=transcription.health(m["url"]))}
    if kind == "translator":
        if m.get("type") == "none":
            return {"ok": True, "detail": i18n.t("test.nothing")}
        return {"ok": True, "detail": i18n.t("test.responds", state=translation.test(m))}
    raise RequestError(400, i18n.t("err.unknown_kind"))


# --- router ---
class Raw(NamedTuple):
    """Response that is not JSON: a file of the page or a translation catalog."""
    body: bytes
    content_type: str


ROUTES: list[tuple[str, re.Pattern, Callable]] = []


def _compile(pattern: str) -> re.Pattern:
    """"/api/tasks/{id}" → {id}: hexadecimal identifier; "{name:regex}": parameter with its own expression."""
    return re.compile(re.sub(r"\{(\w+)(?::([^}]+))?\}", lambda m: f"(?P<{m.group(1)}>{m.group(2) or '[0-9a-f]+'})", pattern))


def route(method: str, pattern: str):
    """Records the decorated function as the answer to `method pattern`; the parameters of the path are passed by name.

    The function receives the Request and returns (code, data): data is sent as JSON, or as is if it is a Raw."""
    regex = _compile(pattern)

    def register(fn):
        ROUTES.append((method, regex, fn))
        return fn
    return register


def resolve(method: str, path: str) -> tuple[Callable, dict] | None:
    for m, regex, fn in ROUTES:
        match = regex.fullmatch(path)
        if m == method and match:
            return fn, match.groupdict()
    return None


class Request:
    MAX_JSON = 5_000_000

    def __init__(self, handler: BaseHTTPRequestHandler, url):
        self.path = url.path
        self.query = parse_qs(url.query)
        self.headers = handler.headers
        self._rfile = handler.rfile
        self.length = max(0, int(handler.headers.get("Content-Length") or 0))
        self.consumed = 0
        self._body = None

    def arg(self, name: str, default=None):
        """First value of a parameter of the query string."""
        return self.query.get(name, [default])[0]

    def read(self, n: int) -> bytes:
        block = self._rfile.read(min(n, self.length - self.consumed))
        self.consumed += len(block)
        return block

    def json(self) -> dict:
        if self._body is None:
            if self.length > self.MAX_JSON:
                raise RequestError(413, i18n.t("err.body_too_large"))
            self._body = json.loads(self.read(self.length) or b"{}")
        return self._body

    def drain(self):
        """Reads what the route has left of the body: a connection closed on unread data is reset (Windows) and the
        client loses the answer."""
        left = self.length - self.consumed
        if 0 < left <= self.MAX_JSON:
            self.read(left)


def _not_found():
    return RequestError(404, i18n.t("err.not_found"))


# --- page, catalogs, state ---
@route("GET", "/")
def page(req):
    return static_file(req, "index.html")


@route("GET", "/static/{name:.+}")
def static_file(req, name):
    f = (STATIC / name).resolve()
    if not f.is_file() or STATIC not in f.parents:
        raise _not_found()
    return 200, Raw(f.read_bytes(), TYPES.get(f.suffix, "application/octet-stream"))


@route("GET", "/locales/{name:.+}")
def catalog(req, name):
    """Translation catalog of a language (the same files as the messages of the server)."""
    code = name.removesuffix(".json")
    if code not in i18n.available():
        raise _not_found()
    return 200, Raw((i18n.LOCALES_DIR / f"{code}.json").read_bytes(), "application/json; charset=utf-8")


@route("GET", "/api/languages")
def languages(req):
    """Available interface languages and setting."""
    setting = config.load().get("ui_language") or ""
    return 200, {"available": i18n.available(), "setting": i18n.pick(setting) or ""}


@route("GET", "/api/health")
def get_health(req):
    """"local service" contract v2: {state, contract, service, version, busy, activity, remaining_work_s, idle}."""
    return 200, health()


@route("GET", "/api/status")
def status(req):
    """Active engines, state of the local transcription server and of the local AI, current task."""
    cfg = config.load()
    current = manager.queue.current() or next(iter(reversed(manager.queue.paused())), None)
    batch = manager.batches.active()
    return 200, {"batch": batch.summary() if batch else None,
                 "active_transcription": cfg.get("active_transcription"), "active_translator": cfg.get("active_translator"),
                 "output_dir": cfg.get("output_dir"), "language": cfg.get("language", ""),
                 "local_transcription": local_transcription_state(cfg), "local_ai": local_ai_state(cfg),
                 "task": current.summary() if current else None, "user_config": str(config.USER_FILE)}


@route("GET", "/api/config")
def get_config(req):
    return 200, config.load()


@route("PUT", "/api/config")
def put_config(req):
    """ui_language: language imposed on the page and the log, empty = automatic."""
    cfg = req.json()
    config.save(cfg)
    i18n.set_language(cfg.get("ui_language"))
    return 200, config.load()


# --- tasks ---
@route("GET", "/api/tasks")
def list_tasks(req):
    return 200, manager.queue.all_tasks()


@route("POST", "/api/tasks")
def create_task(req):
    """{source, transcription?, translator?, language?, languages?, output_dir?, destination?, redo?, pause?, until?, replace?}"""
    d = req.json()
    src = (d.get("source") or "").strip().strip('"')
    if not src:
        raise RequestError(400, i18n.t("err.empty_source"))
    if not source.is_url(src) and Path(src).is_dir():
        return 409, {"error": i18n.t("err.is_folder"), "folder": True}
    options = {"source": src, "transcription": d.get("transcription") or "", "translator": d.get("translator") or "",
               "language": d.get("language"), "output_dir": (d.get("output_dir") or "").strip(), "redo": bool(d.get("redo"))}
    if d.get("languages") is not None:
        options["languages"] = language.normalize_codes(d["languages"])
    if isinstance(d.get("destination"), dict):
        options["destination"] = {k: bool(d["destination"].get(k)) for k in ("next_to_video", "output_dir")}
    if d.get("pause") is not None:
        options["pause"] = bool(d["pause"])
    if d.get("until") == "mux":
        options.update(until="mux", continued=True, replace=bool(d.get("replace")))
    return 201, manager.queue.add(options).summary()


@route("GET", "/api/tasks/{id}")
def get_task(req, id):
    """?since=N: log lines from N."""
    d = manager.queue.detail(id, int(req.arg("since", "0")))
    if d is None:
        raise RequestError(404, i18n.t("err.unknown_task"))
    return 200, d


@route("GET", "/api/tasks/{id}/estimate")
def estimate_task(req, id):
    """?languages=fr,en: estimated cost of a paused task for these languages."""
    e = manager.queue.estimate(id, req.arg("languages"))
    if e is None:
        raise RequestError(404, i18n.t("err.task_not_prepared"))
    return 200, e


@route("POST", "/api/tasks/{id}/translate")
def translate_task(req, id):
    """{translator?, languages?}: second phase of a paused task ("none" = finish without translating)."""
    d = req.json()
    t = manager.queue.resume(id, d.get("translator"), d.get("languages"))
    if t is None:
        raise RequestError(409, i18n.t("err.task_not_paused"))
    return 200, t.summary()


@route("POST", "/api/tasks/{id}/retry")
def retry_task(req, id):
    """Task in error."""
    t = manager.queue.retry(id)
    if t is None:
        raise RequestError(404, i18n.t("err.task_not_in_error"))
    return 200, {"id": t.id}


@route("POST", "/api/tasks/{id}/cancel")
def cancel_task(req, id):
    return 200, {"ok": manager.queue.cancel(id)}


# --- folder inventory (one at a time) ---
@route("POST", "/api/folder")
def scan_folder(req):
    """{path, recursive?}: starts the inventory in the background → {id, state…}."""
    global current_scan
    d = req.json()
    path = Path((d.get("path") or "").strip().strip('"'))
    if not path.is_dir():
        raise RequestError(400, i18n.t("err.folder_not_found", path=path))
    if current_scan:
        current_scan.cancel()
    current_scan = folder.FolderScan(path, recursive=d.get("recursive", True))
    current_scan.start()
    return 202, current_scan.to_dict()


@route("GET", "/api/folder/{id}")
def get_scan(req, id):
    """Progress, languages of the audio tracks; ?videos=1: list of the videos once scanned."""
    if not current_scan or current_scan.id != id:
        raise RequestError(404, i18n.t("err.unknown_analysis"))
    return 200, current_scan.to_dict("videos" in req.query)


@route("POST", "/api/folder/{id}/cancel")
def cancel_scan(req, id):
    if current_scan and current_scan.id == id:
        current_scan.cancel()
    return 200, {"ok": True}


# --- batches ---
@route("GET", "/api/batches")
def list_batches(req):
    return 200, manager.batches.summaries()


@route("POST", "/api/batches")
def create_batch(req):
    """{root, videos: [{path, language}], transcription?, translator?, languages?, destination?, output_dir?, pause?,
    redo?, stop?, review?, replace?}"""
    d = req.json()
    videos = [v for v in d.get("videos") or [] if v.get("path")]
    if not videos:
        raise RequestError(400, i18n.t("err.no_video_selected"))
    options = {k: d.get(k) for k in ("transcription", "translator", "languages", "destination", "output_dir", "pause",
                                     "redo", "stop", "review", "replace") if d.get(k) is not None}
    if "languages" in options:
        options["languages"] = language.normalize_codes(options["languages"])
    _check_stop(options.get("stop"))
    if options.get("stop") == "validation" or options.get("review"):
        options["pause"] = True    # the proposed names are checked before translation, whatever the pause setting
    return 201, manager.batches.create(str(d.get("root") or Path(videos[0]["path"]).parent), videos, options).summary()


@route("GET", "/api/batches/last")
def last_batch(req):
    """Batch left unfinished at the previous session."""
    return 200, {"last": manager.batches.last()}


@route("POST", "/api/batches/last/forget")
def forget_last_batch(req):
    manager.batches.forget_last()
    return 200, {"ok": True}


@route("POST", "/api/batches/load")
def load_batch(req):
    """{file}: reads a subtitles.batch.json back (resume after an interruption) → suspended batch, to resume."""
    try:
        return 200, manager.batches.load((req.json().get("file") or "").strip().strip('"')).summary()
    except source.SourceError as e:
        raise RequestError(400, str(e)) from e


@route("GET", "/api/batches/{id}")
def get_batch(req, id):
    batch = manager.batches.get(id)
    if batch is None:
        raise RequestError(404, i18n.t("err.unknown_batch"))
    return 200, batch.summary()


@route("GET", "/api/batches/{id}/estimate")
def estimate_batch(req, id):
    """?languages=fr,en"""
    if manager.batches.get(id) is None:
        raise RequestError(404, i18n.t("err.unknown_batch"))
    return 200, manager.batches.estimate(id, req.arg("languages")) or {}


@route("GET", "/api/batches/{id}/review")
def review_batch(req, id):
    """?path=: names to check."""
    r = manager.batches.proposals(id, req.arg("path"))
    if r is None:
        raise RequestError(404, i18n.t("err.unknown_batch"))
    return 200, r


@route("POST", "/api/batches/{id}/validate")
def validate_batch(req, id):
    """{decisions: [{transcribed, spelling, french, accept}]}"""
    r = manager.batches.validate_names(id, req.json().get("decisions") or [])
    if r is None:
        raise RequestError(404, i18n.t("err.unknown_batch"))
    return 200, {**r, "batch": manager.batches.get(id).summary()}


def _check_stop(stop):
    if stop not in (None, *steps.STOPS):
        raise RequestError(400, i18n.t("err.unknown_stop_step"))


def _batch_after(id: str, ok: bool, action: str):
    """Answer of an action on a batch: its new state, or 409 if the batch refused it."""
    if not ok:
        raise RequestError(409, i18n.t("err.batch_action", action=action))
    return 200, manager.batches.get(id).summary()


@route("POST", "/api/batches/{id}/{action:suspend|cancel}")
def suspend_or_cancel_batch(req, id, action):
    return _batch_after(id, getattr(manager.batches, action)(id), action)


@route("POST", "/api/batches/{id}/translate")
def translate_batch(req, id):
    """{translator?, languages?, stop?}"""
    d = req.json()
    _check_stop(d.get("stop"))
    return _batch_after(id, manager.batches.translate(id, d.get("translator"), d.get("languages"), d.get("stop")), "translate")


@route("POST", "/api/batches/{id}/resume")
def resume_batch(req, id):
    """{translator?, transcription?, languages?, pause?, stop?}"""
    return _batch_after(id, manager.batches.resume(id, req.json()), "resume")


@route("POST", "/api/batches/{id}/goal")
def batch_goal(req, id):
    """{stop, replace?}: translation (subtitles only) or mux (subtitles + dubbing)."""
    d = req.json()
    return _batch_after(id, manager.batches.goal(id, d.get("stop") or "", d.get("replace")), "goal")


@route("POST", "/api/batches/{id}/continue")
def continue_batch(req, id):
    """{path}: one video up to the end of what the batch must produce, before the others."""
    return _batch_after(id, manager.batches.continue_video(id, req.json().get("path") or ""), "continue")


@route("POST", "/api/batches/{id}/retry")
def retry_batch(req, id):
    """{path?}"""
    return _batch_after(id, manager.batches.retry(id, req.json().get("path")), "retry")


# --- working folders, files ---
@route("GET", "/api/cleanup")
def cleanup_summary(req):
    """?output_dir=: size of the working folders per category."""
    cfg = config.load()
    root = Path(req.arg("output_dir", "") or cfg["output_dir"]) / ".work"
    return 200, {"root": str(root), "categories": cleanup.summary(root), "options": cleanup.options(cfg)}


@route("POST", "/api/cleanup")
def cleanup_run(req):
    """{options, output_dir?} → deleted per category."""
    d = req.json()
    cfg = config.load()
    opts = {**cleanup.options(cfg), **{k: v for k, v in (d.get("options") or {}).items() if k in cleanup.DEFAULTS}}
    root = Path(d.get("output_dir") or cfg["output_dir"]) / ".work"
    if manager.queue.current():
        raise RequestError(409, i18n.t("err.task_running_cleanup"))
    return 200, {"root": str(root), "deleted": cleanup.clean_all(root, opts), "categories": cleanup.summary(root)}


@route("PUT", "/api/files")
def upload_file(req):
    """?name=<name>&output_dir=, body = the file (drag and drop) → copied into the output folder."""
    name = Path(unquote(req.arg("name", ""))).name
    if not name:
        raise RequestError(400, i18n.t("err.missing_name"))
    target_dir = Path(req.arg("output_dir", "") or config.load()["output_dir"])
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / name
    if target.exists() and target.stat().st_size == req.length:
        while req.read(1 << 20):        # already there, same size: not rewritten, but the body is read
            pass
        return 200, {"path": str(target), "existed": True}
    with open(target, "wb") as f:
        while block := req.read(1 << 20):
            f.write(block)
    if req.consumed < req.length:
        target.unlink(missing_ok=True)
        raise RequestError(400, i18n.t("err.incomplete_upload"))
    return 200, {"path": str(target), "existed": False}


# --- system, engines ---
@route("POST", "/api/local-transcription/stop")
def stop_local_transcription(req):
    """Stops the local transcription server started from here."""
    for m in config.load().get("transcriptions", []):
        if m.get("command"):
            local_transcription.LocalServer.for_engine(m).stop()
    return 200, {"ok": True}


@route("POST", "/api/dialog")
def system_dialog(req):
    """{type: "file"|"folder"|"batch"|"source_folder", start?} → {path}"""
    d = req.json()
    return 200, {"path": dialog(d.get("type", "file"), d.get("start", ""))}


@route("POST", "/api/open")
def open_in_file_manager(req):
    """{path}: opens the folder (file selected) in the file manager."""
    open_path(req.json().get("path", ""))
    return 200, {"ok": True}


@route("POST", "/api/test")
def test_an_engine(req):
    """{kind: "transcription"|"translator", engine: {...}}"""
    d = req.json()
    try:
        return 200, test_engine(d.get("kind", ""), d.get("engine") or {})
    except (transcription.TranscriptionError, translation.TranslationError, config.ConfigError,
            local_transcription.LocalServerError) as e:
        return 200, {"ok": False, "detail": str(e)}


@route("POST", "/api/youtube")
def youtube(req):
    """{url, output_dir?}: title, duration and target file, without downloading."""
    d = req.json()
    cfg = config.load()
    try:
        i = source.youtube_info(d.get("url", ""), cfg.get("youtube", {}).get("command", ["yt-dlp"]),
                                Path(d.get("output_dir") or cfg["output_dir"]))
    except source.SourceError as e:
        return 200, {"ok": False, "detail": str(e)}
    return 200, {"ok": True, "title": i.get("title"), "duration": i.get("duration"), "video": str(i["video"]),
                 "exists": i["video"].exists()}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "subtitles"

    def parse_request(self):
        ok = super().parse_request()
        if ok:
            page_language = self.headers.get("X-Language")
            i18n.for_request(page_language)
            i18n.note_page_language(page_language)
        if ok and is_activity(self.command, self.path):
            idle.touch()
        return ok

    def log_message(self, fmt, *args):
        if self.path.startswith("/api/tasks/") and self.command == "GET":
            return
        log.info("%s %s", self.command, self.path)

    def do_GET(self):
        self._dispatch()

    do_POST = do_PUT = do_GET

    def _foreign(self) -> bool:
        """Request that does not come from the page of the interface: refused before any route.

        Host other than 127.0.0.1 or localhost on our port: DNS rebinding (a site whose name now points to this PC).
        POST or PUT with the Origin of another site: a page open in the browser that writes to the API (CSRF); a
        browser always sends Origin with these methods, other clients (curl, command line) do not send it."""
        port = self.server.server_address[1]
        ours = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if (self.headers.get("Host") or "").lower() not in ours:
            return True
        origin = (self.headers.get("Origin") or "").lower()
        return self.command != "GET" and bool(origin) and origin.removeprefix("http://") not in ours

    def _dispatch(self):
        url = urlparse(self.path)
        req = None
        try:
            if self._foreign():
                raise RequestError(403, i18n.t("err.foreign_request"))
            req = Request(self, url)
            found = resolve(self.command, url.path)
            if found is None:
                raise _not_found()
            fn, params = found
            code, data = fn(req, **params)
        except RequestError as e:
            code, data = e.code, {"error": str(e)}
        except config.ConfigError as e:
            code, data = 400, {"error": str(e)}
        except (ValueError, KeyError) as e:
            code, data = 400, {"error": i18n.t("err.invalid_request", error=e)}
        if req:
            req.drain()
        self._send(code, data)

    def _send(self, code: int, data):
        if isinstance(data, Raw):
            body, content_type = data
        else:
            body, content_type = json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if isinstance(data, Raw):
            self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)


def _watch_idle(server):
    while True:
        time.sleep(1)
        if idle.expired():
            log.info(i18n.t("srv.idle_stop", min=f"{idle.timeout_s() / 60:.0f}"))
            server.shutdown()
            return


def serve(port: int, idle_min: float | None = None):
    global _imposed_timeout
    if idle_min is not None:
        _imposed_timeout = idle_min * 60
    idle.touch()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", stream=sys.stdout)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    try:
        cfg = config.load()
        i18n.set_language(cfg.get("ui_language"))
        count, size = cleanup.purge_logs(Path(cfg["output_dir"]) / ".work", float(cleanup.options(cfg)["logs_age_h"]))
        if count:
            log.info(i18n.t("srv.logs_purged", n=count, mb=f"{size / 1e6:.1f}"))
    except (config.ConfigError, OSError, ValueError, KeyError) as e:
        log.warning(i18n.t("srv.purge_failed", error=e))
    log.info(i18n.t("srv.listening", url=f"http://127.0.0.1:{port}/"))
    threading.Thread(target=_watch_idle, args=(server,), daemon=True, name="idle").start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        manager.close()
        for m in config.load().get("transcriptions", []):
            if m.get("command"):
                local_transcription.LocalServer.for_engine(m).stop()
