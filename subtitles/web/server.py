"""Local HTTP server of the interface (standard library, like the supervision).

  GET  /                         the page
  GET  /api/health               "local service" contract v2: {state, contract, service, version, busy, activity, remaining_work_s, idle}
  GET  /api/status               active engines, state of the local transcription server and of the local AI, current task
  GET  /api/config  PUT /api/config      (ui_language: language imposed on the page and the log, empty = automatic)
  GET  /api/languages            available interface languages and setting     GET /locales/<code>.json   catalog
  POST /api/tasks  {source, transcription?, translator?, language?, languages?, output_dir?, destination?, redo?, pause?}   GET /api/tasks
  GET  /api/tasks/<id>?since=N     POST /api/tasks/<id>/cancel     POST /api/tasks/<id>/retry (task in error)
  GET  /api/tasks/<id>/estimate?languages=fr,en      estimated cost (paused task) for these languages
  POST /api/tasks/<id>/translate {translator?, languages?}   second phase of a paused task ("none" = finish without translating)
  GET  /api/cleanup              size of the working folders per category     POST /api/cleanup {options} → deleted per category
  POST /api/folder {path}        starts the inventory of a folder in the background → {id, state…}
  GET  /api/folder/<id>[?videos=1]   progress, languages of the audio tracks, list of the videos once scanned
  POST /api/folder/<id>/cancel
  POST /api/batches {root, videos: [{path, language}], transcription?, translator?, languages?, destination?, output_dir?, pause?}
  GET  /api/batches   GET /api/batches/<id>   GET /api/batches/<id>/estimate?languages=
  POST /api/batches/load {file}  reads a subtitles.batch.json back (resume after an interruption) → suspended batch, to resume
  POST /api/batches/<id>/suspend | cancel | translate {translator?, languages?, stop?} | resume {translator?, transcription?, languages?, pause?, stop?}
  POST /api/batches/<id>/continue {path}   one video up to the end of what the batch must produce, before the others
  POST /api/batches/<id>/goal {stop}       translation (subtitles only) or mux (subtitles + dubbing)
  GET  /api/batches/<id>/review?path=   names to check     POST /api/batches/<id>/validate {decisions: [{transcribed, spelling, french, accept}]}
  GET  /api/batches/last         batch left unfinished at the previous session     POST /api/batches/last/forget
  PUT  /api/files?name=<name>    body = the file (drag and drop) → copied into the output folder
  POST /api/local-transcription/stop   stops the local transcription server started from here
  POST /api/dialog {type: "file"|"folder"|"batch"|"source_folder", start?}   system dialog box → {path}
  POST /api/open {path}          opens the folder (file selected) in the file manager
  POST /api/test {kind: "transcription"|"translator", engine: {...}}
  POST /api/youtube {url}        title, duration and target file, without downloading
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
from urllib.parse import parse_qs, unquote, urlparse

from .. import __version__, cleanup, config, folder, i18n, language, local_transcription, source, steps, transcription, translation
from .idle import IdleWatch, is_activity
from .tasks import Manager

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

    # --- helpers ---
    def _json(self, code: int, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 5_000_000:
            raise RequestError(413, i18n.t("err.body_too_large"))
        return json.loads(self.rfile.read(n) or b"{}")

    def _file(self, path: str):
        name = "index.html" if path in ("", "/") else path.removeprefix("/static/")
        f = (STATIC / name).resolve()
        if not f.is_file() or STATIC not in f.parents:
            return self._json(404, {"error": i18n.t("err.not_found")})
        body = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", TYPES.get(f.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _catalog(self, name: str):
        """Translation catalog of a language (the same files as the messages of the server)."""
        code = name.removesuffix(".json")
        if code not in i18n.available():
            return self._json(404, {"error": i18n.t("err.not_found")})
        body = (i18n.LOCALES_DIR / f"{code}.json").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # --- routes ---
    def do_GET(self):
        u = urlparse(self.path)
        try:
            if u.path == "/" or u.path.startswith("/static/"):
                return self._file(u.path)
            if u.path.startswith("/locales/"):
                return self._catalog(u.path.removeprefix("/locales/"))
            if u.path == "/api/languages":
                setting = config.load().get("ui_language") or ""
                return self._json(200, {"available": i18n.available(), "setting": i18n.pick(setting) or ""})
            if u.path == "/api/health":
                return self._json(200, health())
            cfg = config.load()
            if u.path == "/api/status":
                current = manager.current() or next(iter(reversed(manager.paused())), None)
                running = current.summary() if current else None
                batch = manager.active_batch()
                return self._json(200, {"batch": batch.summary() if batch else None,
                                        "active_transcription": cfg.get("active_transcription"), "active_translator": cfg.get("active_translator"),
                                        "output_dir": cfg.get("output_dir"), "language": cfg.get("language", ""),
                                        "local_transcription": local_transcription_state(cfg), "local_ai": local_ai_state(cfg),
                                        "task": running, "user_config": str(config.USER_FILE)})
            if u.path == "/api/config":
                return self._json(200, cfg)
            if u.path == "/api/tasks":
                return self._json(200, manager.all_tasks())
            if u.path == "/api/cleanup":
                root = Path(parse_qs(u.query).get("output_dir", [""])[0] or cfg["output_dir"]) / ".work"
                return self._json(200, {"root": str(root), "categories": cleanup.summary(root), "options": cleanup.options(cfg)})
            if u.path == "/api/batches":
                return self._json(200, [b.summary() for b in reversed(list(manager.batches.values()))])
            if u.path == "/api/batches/last":
                return self._json(200, {"last": manager.last_batch()})
            m = re.fullmatch(r"/api/folder/([0-9a-f]+)", u.path)
            if m:
                if not current_scan or current_scan.id != m.group(1):
                    return self._json(404, {"error": i18n.t("err.unknown_analysis")})
                return self._json(200, current_scan.to_dict("videos" in parse_qs(u.query)))
            m = re.fullmatch(r"/api/batches/([0-9a-f]+)/review", u.path)
            if m:
                r = manager.batch_proposals(m.group(1), parse_qs(u.query).get("path", [None])[0])
                return self._json(200, r) if r is not None else self._json(404, {"error": i18n.t("err.unknown_batch")})
            m = re.fullmatch(r"/api/batches/([0-9a-f]+)(/estimate)?", u.path)
            if m:
                batch = manager.batch(m.group(1))
                if batch is None:
                    return self._json(404, {"error": i18n.t("err.unknown_batch")})
                if m.group(2):
                    q = parse_qs(u.query)
                    return self._json(200, manager.estimate_batch(batch.id, q["languages"][0] if "languages" in q else None) or {})
                return self._json(200, batch.summary())
            m = re.fullmatch(r"/api/tasks/([0-9a-f]+)/estimate", u.path)
            if m:
                q = parse_qs(u.query)
                e = manager.estimate(m.group(1), q["languages"][0] if "languages" in q else None)
                if e is None:
                    return self._json(404, {"error": i18n.t("err.task_not_prepared")})
                return self._json(200, e)
            m = re.fullmatch(r"/api/tasks/([0-9a-f]+)", u.path)
            if m:
                d = manager.detail(m.group(1), int(parse_qs(u.query).get("since", ["0"])[0]))
                if d is None:
                    return self._json(404, {"error": i18n.t("err.unknown_task")})
                return self._json(200, d)
            return self._json(404, {"error": i18n.t("err.not_found")})
        except RequestError as e:
            return self._json(e.code, {"error": str(e)})

    def do_POST(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/tasks":
                d = self._json_body()
                src = (d.get("source") or "").strip().strip('"')
                if not src:
                    raise RequestError(400, i18n.t("err.empty_source"))
                if not source.is_url(src) and Path(src).is_dir():
                    return self._json(409, {"error": i18n.t("err.is_folder"), "folder": True})
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
                t = manager.add(options)
                return self._json(201, t.summary())
            m = re.fullmatch(r"/api/tasks/([0-9a-f]+)/translate", u.path)
            if m:
                d = self._json_body()
                t = manager.resume(m.group(1), d.get("translator"), d.get("languages"))
                if t is None:
                    raise RequestError(409, i18n.t("err.task_not_paused"))
                return self._json(200, t.summary())
            if u.path == "/api/folder":
                d = self._json_body()
                path = Path((d.get("path") or "").strip().strip('"'))
                if not path.is_dir():
                    raise RequestError(400, i18n.t("err.folder_not_found", path=path))
                global current_scan
                if current_scan:
                    current_scan.cancel()
                current_scan = folder.FolderScan(path, recursive=d.get("recursive", True))
                current_scan.start()
                return self._json(202, current_scan.to_dict())
            m = re.fullmatch(r"/api/folder/([0-9a-f]+)/cancel", u.path)
            if m:
                if current_scan and current_scan.id == m.group(1):
                    current_scan.cancel()
                return self._json(200, {"ok": True})
            if u.path == "/api/batches":
                d = self._json_body()
                videos = [v for v in d.get("videos") or [] if v.get("path")]
                if not videos:
                    raise RequestError(400, i18n.t("err.no_video_selected"))
                options = {k: d.get(k) for k in ("transcription", "translator", "languages", "destination", "output_dir", "pause", "redo", "stop", "review", "replace") if d.get(k) is not None}
                if "languages" in options:
                    options["languages"] = language.normalize_codes(options["languages"])
                if options.get("stop") not in (None, *steps.STOPS):
                    raise RequestError(400, i18n.t("err.unknown_stop_step"))
                if options.get("stop") == "validation" or options.get("review"):
                    options["pause"] = True    # the proposed names are checked before translation, whatever the pause setting
                return self._json(201, manager.create_batch(str(d.get("root") or Path(videos[0]["path"]).parent), videos, options).summary())
            if u.path == "/api/batches/last/forget":
                manager.forget_last_batch()
                return self._json(200, {"ok": True})
            if u.path == "/api/batches/load":
                d = self._json_body()
                try:
                    return self._json(200, manager.load_batch((d.get("file") or "").strip().strip('"')).summary())
                except source.SourceError as e:
                    raise RequestError(400, str(e)) from e
            m = re.fullmatch(r"/api/batches/([0-9a-f]+)/validate", u.path)
            if m:
                r = manager.validate_names(m.group(1), self._json_body().get("decisions") or [])
                if r is None:
                    raise RequestError(404, i18n.t("err.unknown_batch"))
                return self._json(200, {**r, "batch": manager.batch(m.group(1)).summary()})
            m = re.fullmatch(r"/api/batches/([0-9a-f]+)/(suspend|resume|cancel|translate|retry|continue|goal)", u.path)
            if m:
                ident, action = m.groups()
                if action == "translate":
                    d = self._json_body()
                    if d.get("stop") not in (None, *steps.STOPS):
                        raise RequestError(400, i18n.t("err.unknown_stop_step"))
                    ok = manager.batch_translate(ident, d.get("translator"), d.get("languages"), d.get("stop"))
                elif action == "goal":
                    d = self._json_body()
                    ok = manager.batch_goal(ident, d.get("stop") or "", d.get("replace"))
                elif action == "continue":
                    ok = manager.batch_continue(ident, self._json_body().get("path") or "")
                elif action == "retry":
                    ok = manager.batch_retry(ident, self._json_body().get("path"))
                elif action == "resume":
                    ok = manager.batch_resume(ident, self._json_body())
                else:
                    ok = getattr(manager, "batch_" + action)(ident)
                if not ok:
                    raise RequestError(409, i18n.t("err.batch_action", action=action))
                return self._json(200, manager.batch(ident).summary())
            if u.path == "/api/cleanup":
                d = self._json_body()
                cfg = config.load()
                opts = {**cleanup.options(cfg), **{k: v for k, v in (d.get("options") or {}).items() if k in cleanup.DEFAULTS}}
                root = Path(d.get("output_dir") or cfg["output_dir"]) / ".work"
                if manager.current():
                    raise RequestError(409, i18n.t("err.task_running_cleanup"))
                return self._json(200, {"root": str(root), "deleted": cleanup.clean_all(root, opts),
                                        "categories": cleanup.summary(root)})
            m = re.fullmatch(r"/api/tasks/([0-9a-f]+)/retry", u.path)
            if m:
                t = manager.retry(m.group(1))
                return self._json(200, {"id": t.id}) if t else self._json(404, {"error": i18n.t("err.task_not_in_error")})
            m = re.fullmatch(r"/api/tasks/([0-9a-f]+)/cancel", u.path)
            if m:
                return self._json(200, {"ok": manager.cancel(m.group(1))})
            if u.path == "/api/local-transcription/stop":
                cfg = config.load()
                for m in cfg.get("transcriptions", []):
                    if m.get("command"):
                        local_transcription.LocalServer.for_engine(m).stop()
                return self._json(200, {"ok": True})
            if u.path == "/api/dialog":
                d = self._json_body()
                return self._json(200, {"path": dialog(d.get("type", "file"), d.get("start", ""))})
            if u.path == "/api/open":
                open_path(self._json_body().get("path", ""))
                return self._json(200, {"ok": True})
            if u.path == "/api/test":
                d = self._json_body()
                try:
                    return self._json(200, test_engine(d.get("kind", ""), d.get("engine") or {}))
                except (transcription.TranscriptionError, translation.TranslationError, config.ConfigError,
                        local_transcription.LocalServerError) as e:
                    return self._json(200, {"ok": False, "detail": str(e)})
            if u.path == "/api/youtube":
                d = self._json_body()
                cfg = config.load()
                try:
                    i = source.youtube_info(d.get("url", ""), cfg.get("youtube", {}).get("command", ["yt-dlp"]),
                                            Path(d.get("output_dir") or cfg["output_dir"]))
                except source.SourceError as e:
                    return self._json(200, {"ok": False, "detail": str(e)})
                return self._json(200, {"ok": True, "title": i.get("title"), "duration": i.get("duration"), "video": str(i["video"]),
                                        "exists": i["video"].exists()})
            return self._json(404, {"error": i18n.t("err.not_found")})
        except RequestError as e:
            return self._json(e.code, {"error": str(e)})
        except (ValueError, KeyError) as e:
            return self._json(400, {"error": i18n.t("err.invalid_request", error=e)})

    def do_PUT(self):
        u = urlparse(self.path)
        try:
            if u.path == "/api/config":
                cfg = self._json_body()
                config.save(cfg)
                i18n.set_language(cfg.get("ui_language"))
                return self._json(200, config.load())
            if u.path == "/api/files":
                name = Path(unquote(parse_qs(u.query).get("name", [""])[0])).name
                if not name:
                    raise RequestError(400, i18n.t("err.missing_name"))
                cfg = config.load()
                target_dir = Path(parse_qs(u.query).get("output_dir", [""])[0] or cfg["output_dir"])
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / name
                size = int(self.headers.get("Content-Length") or 0)
                if target.exists() and target.stat().st_size == size:
                    self.rfile.read(size)       # already there, same size: not rewritten
                    return self._json(200, {"path": str(target), "existed": True})
                left = size
                with open(target, "wb") as f:
                    while left > 0:
                        block = self.rfile.read(min(1 << 20, left))
                        if not block:
                            break
                        f.write(block)
                        left -= len(block)
                if left:
                    target.unlink(missing_ok=True)
                    raise RequestError(400, i18n.t("err.incomplete_upload"))
                return self._json(200, {"path": str(target), "existed": False})
            return self._json(404, {"error": i18n.t("err.not_found")})
        except RequestError as e:
            return self._json(e.code, {"error": str(e)})
        except config.ConfigError as e:
            return self._json(400, {"error": str(e)})
        except (ValueError, KeyError) as e:
            return self._json(400, {"error": i18n.t("err.invalid_request", error=e)})


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
