"""Queue of subtitling tasks: one at a time (the transcription server and the local AI only handle one), except the
translation of a batch by a remote API, which moves forward on several videos (`translation.concurrency`).

Two phases (see pipeline): preparation (source, audio, transcription, original subtitles), then translation and
copies. With the `pause` option, the task stops between both (state `paused`) with a cost estimate; `resume()`
starts the second phase, possibly with another translator or other languages.
History (last 50 tasks, summaries) in %LOCALAPPDATA%/subtitles/history.json; the full log of each task is written
at its end in the working folder of the video (.work/<name>/log-<date>-<id>.txt) and read back from there for the
tasks of previous sessions."""
import json
import subprocess
import threading
import time
import traceback
import uuid
from pathlib import Path

from .. import (config, dubbing, estimation, folder, glossary, i18n, language, local_transcription, pipeline, review, source, steps,
                transcription, translation)
from . import timing
from .batches import Batch

HISTORY_FILE = config.user_dir() / "history.json"
LAST_BATCH_FILE = config.user_dir() / "last_batch.json"   # path of the file of the last unfinished batch
KNOWN_ERRORS = (source.SourceError, transcription.TranscriptionError, translation.TranslationError,
                config.ConfigError, local_transcription.LocalServerError, dubbing.DubbingError)
ACTIVE_STATES = ("queued", "running")
FINISHED_STATES = ("done", "error", "cancelled")


class Task:
    def __init__(self, options: dict):
        self.id = uuid.uuid4().hex[:8]
        self.options = options                 # source, transcription, translator, language, languages, output_dir, destination, redo, pause
        self.state = "queued"                  # queued, running, paused, done, error, cancelled
        self.step = ""
        self.log: list[str] = []
        self.result: dict | None = None
        self.error = ""
        self.created = time.time()
        self.started = None
        self.finished = None
        self.cancel_requested = False
        self.work: Path | None = None          # .work/<name> folder, known after the source step
        self.log_file: str | None = None
        self.prepared: pipeline.Prepared | None = None   # first phase done
        self.estimate: dict | None = None
        self.video_duration = float(options.get("video_duration") or 0)   # known in advance in a batch, otherwise when the audio is extracted
        self.pass_no, self.pass_started = 1, None  # see timing.py
        self.glossary_seen = 0.0               # state of the glossary at segmentation time
        self.progress: dict | None = None      # {done, total}: subtitles translated in the current language
        self.log_resumed_at = 0                # first log line after the last resume (parallel follow-up)

    def write(self, msg: str):
        self.log.append(time.strftime("[%H:%M:%S] ") + msg)

    def weight(self) -> float:
        if self.pass_no in (1, 3):
            return self.video_duration
        return len(self.prepared.cues) * max(1, len(self.options.get("languages") or [])) if self.prepared else 0.0

    def remaining_s(self, now: float | None = None) -> int | None:
        """Seconds before the end of this pass; None if the task is not working or if nothing allows an estimate."""
        if self.state != "running" or self.pass_started is None or self.weight() <= 0:
            return None
        rate = timing.known_rate(self.pass_no, self.options)
        if not rate:
            return None
        return round(max(0.0, self.weight() * rate - ((time.time() if now is None else now) - self.pass_started)))

    def summary(self) -> dict:
        return {"id": self.id, "source": self.options.get("source", ""), "state": self.state, "step": self.step,
                "created": self.created, "started": self.started, "finished": self.finished, "error": self.error, "result": self.result,
                "log_file": self.log_file, "estimate": self.estimate, "remaining_s": self.remaining_s(), "pass_started": self.pass_started,
                "progress": self.progress, "log_resumed_at": self.log_resumed_at,
                "duration_s": None if self.state in ACTIVE_STATES else timing.log_duration(self.log),
                "prepared": {"video": str(self.prepared.video), "language": self.prepared.code, "count": len(self.prepared.cues),
                             "source_srt": str(self.prepared.source_srt)} if self.prepared else None,
                "options": {k: v for k, v in self.options.items() if k != "source"}}

    def detail(self, since: int = 0) -> dict:
        d = self.summary()
        d["log"] = self.log[since:]
        d["log_total"] = len(self.log)
        return d


class Manager:
    def __init__(self):
        self.tasks: dict[str, Task] = {}
        self.order: list[str] = []
        self.batches: dict[str, Batch] = {}
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.history = self._load_history()
        self._durations: dict[tuple, int | None] = {}
        threading.Thread(target=self._loop, daemon=True, name="tasks").start()

    # --- API ---
    def add(self, options: dict) -> Task:
        t = Task(options)
        with self.lock:
            self.tasks[t.id] = t
            self.order.append(t.id)
        t.write(i18n.t("log.waiting", source=options.get("source")))
        self.wake.set()
        return t

    def retry(self, ident: str) -> Task | None:
        """New task with the same settings for a single video in error; None if it is not possible. What is already done is reused."""
        t = self.tasks.get(ident)
        r = t.summary() if t else next((x for x in self.history if x["id"] == ident), None)
        if not r or r["state"] != "error" or (r.get("options") or {}).get("batch"):
            return None
        return self.add({**(r.get("options") or {}), "source": r["source"]})

    def get(self, ident: str) -> Task | None:
        return self.tasks.get(ident)

    def detail(self, ident: str, since: int = 0) -> dict | None:
        """Task in memory, or archived task (history summary + log read back from its file)."""
        t = self.tasks.get(ident)
        if t:
            return t.detail(since)
        h = next((x for x in self.history if x["id"] == ident), None)
        if not h:
            return None
        lines = []
        if h.get("log_file"):
            try:
                lines = Path(h["log_file"]).read_text(encoding="utf-8").splitlines()
            except OSError:
                lines = [i18n.t("log.journal_missing", path=h["log_file"])]
        return {**h, "log": lines[since:], "log_total": len(lines), "duration_s": timing.log_duration(lines)}

    def cancel(self, ident: str) -> bool:
        t = self.tasks.get(ident)
        if not t or t.state not in ACTIVE_STATES + ("paused",):
            return False
        t.cancel_requested = True
        if t.state in ("queued", "paused"):
            t.state, t.finished = "cancelled", time.time()
            t.write(i18n.t("log.cancelled_before_start") if t.prepared is None else i18n.t("log.cancelled_in_pause"))
            self._write_log(t)
            self._archive(t)
            if t.options.get("batch"):
                self._update_batch(t)
        else:
            t.write(i18n.t("log.cancel_requested"))
        return True

    def resume(self, ident: str, translator: str | None = None, languages: list[str] | None = None) -> Task | None:
        """Second phase of a paused task, possibly with another translator or other languages."""
        t = self.tasks.get(ident)
        if not t or t.state != "paused":
            return None
        if translator is not None:
            t.options["translator"] = translator
        if languages is not None:
            t.options["languages"] = language.normalize_codes(languages)
        t.state = "queued"
        t.log_resumed_at = len(t.log)
        t.write(i18n.t("log.resume_prefix") + " " + i18n.t("log.resumed", translator=t.options.get("translator") or i18n.t("log.active_translator"),
                                                           langs=", ".join(t.options.get("languages") or []) or i18n.t("log.default_languages")))
        self.wake.set()
        return t

    def estimate(self, ident: str, languages: list[str] | None = None) -> dict | None:
        t = self.tasks.get(ident)
        if not t or not t.prepared:
            return None
        cfg = config.load()
        wanted = language.normalize_codes(languages) if languages is not None else pipeline.target_languages(self._request(t, cfg))
        remaining = pipeline.remaining_languages(t.prepared, wanted, bool(t.options.get("redo")))
        return estimation.estimate(cfg, len(t.prepared.cues), remaining, t.prepared.code)

    def current(self) -> Task | None:
        return next((t for t in self.tasks.values() if t.state in ACTIVE_STATES), None)

    def busy(self) -> bool:
        """A task runs or waits for its turn, or a batch chains its videos."""
        return self.current() is not None or any(b.state == "running" for b in self.batches.values())

    def activity(self) -> str:
        """What is moving forward, in a few words ("local service" contract): "batch 3/22" or the step of the task; empty if nothing."""
        batch = next((b for b in list(self.batches.values()) if b.state == "running"), None)
        if batch:
            return batch.progress_text()
        t = self.current()
        return (t.step or i18n.t("activity.task")) if t else ""

    def remaining_s(self) -> int | None:
        """Seconds before the end of the running work (batch, otherwise single task), if it can be estimated."""
        batch = next((b for b in list(self.batches.values()) if b.state == "running"), None)
        if batch:
            return batch.remaining_s(timing.known_rate(batch.phase, batch.options))
        t = self.current()
        return t.remaining_s() if t else None

    def close(self):
        """When the service stops: the paused single tasks are archived with their log. Those of a batch stay
        described by the batch file, from which it is resumed. In both cases transcription.json avoids transcribing again."""
        for t in self.paused():
            if not t.options.get("batch"):
                t.write(i18n.t("log.stopped_in_pause"))
                t.state, t.finished = "cancelled", time.time()
                self._write_log(t)
                self._archive(t)

    def paused(self) -> list[Task]:
        return [self.tasks[i] for i in self.order if self.tasks[i].state == "paused"]

    def all_tasks(self) -> list[dict]:
        current = [self.tasks[i].summary() for i in reversed(self.order)]
        seen = {t["id"] for t in current}
        return current + [{**h, "duration_s": self._archived_duration(h)} for h in self.history if h["id"] not in seen]

    def _archived_duration(self, h: dict) -> int | None:
        """Duration of the pass of an archived task, read once in its log (see timing.log_duration)."""
        k = (h["id"], h.get("state"))
        if k not in self._durations:
            try:
                self._durations[k] = timing.log_duration(Path(h["log_file"]).read_text(encoding="utf-8").splitlines()) if h.get("log_file") else None
            except OSError:
                self._durations[k] = None
        return self._durations[k]

    # --- Execution ---
    def _loop(self):
        while True:
            self.wake.wait(timeout=5)
            self.wake.clear()
            for ident in list(self.order):
                t = self.tasks[ident]
                if t.state == "queued":
                    self._start(t)
            self._feed_batches()

    def _start(self, t: Task):
        """One task at a time, except the translations of a batch by a remote API: each one in its thread."""
        batch = self.batches.get(t.options.get("batch") or "")
        if batch and batch.concurrency > 1 and t.prepared is not None and not steps.goes_to(t.options.get("until"), "dubbing"):   # a single graphics card
            t.state = "running"                                # before the thread: the loop must not start it again
            threading.Thread(target=self._run, args=(t,), daemon=True, name=f"task-{t.id}").start()
        else:
            self._run(t)

    # --- Batches (source folder) ---
    def create_batch(self, root: str, videos: list[dict], options: dict) -> Batch:
        cfg = config.load()
        dest = {**pipeline.DEFAULT_DESTINATION, **(cfg.get("destination") or {}), **(options.get("destination") or {})}
        file = folder.batch_path(Path(root), Path(options.get("output_dir") or cfg["output_dir"]), bool(dest.get("output_dir")))
        items = [{"path": v["path"], "name": Path(v["path"]).name, "language": v.get("language") or "", "state": "todo",
                  "task": None, "count": None, "detected_language": "", "error": ""} for v in videos]
        batch = Batch(root, items, options, file)
        batch.after_save, batch.step_of = self._remember_last_batch, self._task_step
        with self.lock:
            self.batches[batch.id] = batch
        self._mark_transcribed(batch)
        batch.save()
        self._measure_durations(batch)
        self.wake.set()
        return batch

    @staticmethod
    def _mark_transcribed(batch: Batch):
        """Videos whose transcription is already there: `Batch.next_video` takes them before those to transcribe."""
        output_dir = batch.options.get("output_dir") or config.load()["output_dir"]
        for v in batch.videos:
            v["transcribed"] = not batch.options.get("redo") and pipeline.transcription_kept(output_dir, v["path"])
        Manager._observe(batch)

    @staticmethod
    def _observe(batch: Batch, videos: list[dict] | None = None):
        """Steps done per video, read from the disk: dots of the list and choice of "Continue"."""
        cfg = config.load()
        output_dir = batch.options.get("output_dir") or cfg["output_dir"]
        languages = language.normalize_codes(batch.options.get("languages") or cfg.get("target_languages", ["fr"]))
        pending = review.pending_names(batch.file)
        for v in videos if videos is not None else batch.videos:
            done = steps.observe(pipeline.work_dir(output_dir, v["path"]), v["path"], languages, v.get("detected_language") or v.get("language") or "", pending)
            done.pop("transcribed")            # computed by _mark_transcribed (takes "redo" into account) or at the end of the task
            v.update(done)

    @staticmethod
    def _measure_durations(batch: Batch):
        """Duration of each video, in the background: the time left takes the longer ones into account."""
        def measure():
            for v in batch.videos:
                if not v.get("duration"):
                    try:
                        v["duration"] = source.duration(Path(v["path"]))
                    except (source.SourceError, OSError, subprocess.SubprocessError):
                        pass
        threading.Thread(target=measure, daemon=True, name="batch-durations").start()

    def load_batch(self, file: str) -> Batch:
        batch = Batch.from_file(Path(file))
        batch.after_save, batch.step_of = self._remember_last_batch, self._task_step
        for other in list(self.batches.values()):              # a single batch per file
            if other.file == batch.file and other.state not in ("done", "cancelled"):
                self.batch_cancel(other.id)                    # outside the lock: cancelling a task archives it, which takes it
        with self.lock:
            self.batches[batch.id] = batch
        self._mark_transcribed(batch)
        batch.save()
        self._measure_durations(batch)
        return batch

    # --- Last batch: offered for resuming at the start of the next session ---
    def _remember_last_batch(self, batch: Batch):
        try:
            if batch.state not in ("done", "cancelled"):
                LAST_BATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
                LAST_BATCH_FILE.write_text(json.dumps({"file": str(batch.file)}, ensure_ascii=False), encoding="utf-8")
            elif self._last_batch_file() == str(batch.file):
                LAST_BATCH_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _last_batch_file() -> str:
        try:
            return json.loads(LAST_BATCH_FILE.read_text(encoding="utf-8")).get("file", "")
        except (OSError, ValueError):
            return ""

    def last_batch(self) -> dict | None:
        """Batch left unfinished at the end of the previous session, or None (finished, already open, or file gone)."""
        file = self._last_batch_file()
        if not file:
            return None
        try:
            d = folder.read_batch(Path(file))
        except source.SourceError:
            return None
        if d.get("state") in ("done", "cancelled") or d.get("id") in self.batches or all(v.get("state") == "done" for v in d["videos"]):
            return None
        return {"file": d["file"], "root": d.get("root", ""), "state": d.get("state", ""), "updated": d.get("updated", ""),
                "total": len(d["videos"]), "done": sum(1 for v in d["videos"] if v.get("state") == "done"),
                "translator": (d.get("options") or {}).get("translator", "")}

    def forget_last_batch(self):
        """"Ignore": the batch is no longer offered; its file stays, for "Resume a batch…"."""
        LAST_BATCH_FILE.unlink(missing_ok=True)

    def _task_step(self, ident: str | None) -> str:
        t = self.tasks.get(ident or "")
        return t.step if t and t.state == "running" else ""

    def batch(self, ident: str) -> Batch | None:
        return self.batches.get(ident)

    def active_batch(self) -> Batch | None:
        return next((b for b in reversed(list(self.batches.values())) if b.state not in ("done", "cancelled")), None)

    def batch_suspend(self, ident: str) -> bool:
        batch = self.batches.get(ident)
        if not batch or batch.state != "running":
            return False
        batch.state = "suspended"                              # the running video goes on to its end (or its pause)
        batch.save()
        return True

    def batch_resume(self, ident: str, changes: dict | None = None) -> bool:
        """Starts a suspended batch again; `changes` (translator, transcription, languages, pause) apply to the videos not started yet."""
        batch = self.batches.get(ident)
        if not batch or batch.state != "suspended":
            return False
        changes = changes or {}
        for k in ("translator", "transcription"):
            if changes.get(k):
                batch.options[k] = changes[k]
        if changes.get("languages") is not None:
            batch.options["languages"] = language.normalize_codes(changes["languages"])
        if changes.get("pause") is not None:
            batch.options["pause"] = bool(changes["pause"])
        if changes.get("stop") in steps.STOPS:
            batch.options["stop"] = changes["stop"]
        batch.state = "running"
        batch.save()
        self.wake.set()
        return True

    def batch_translate(self, ident: str, translator: str | None, languages: list[str] | None, stop: str | None = None) -> bool:
        batch = self.batches.get(ident)
        if not batch or batch.state != "awaiting_translation":
            return False
        if translator is not None:
            batch.options["translator"] = translator
        if languages is not None:
            batch.options["languages"] = language.normalize_codes(languages)
        if stop:
            batch.options["stop"] = stop
        batch.phase, batch.state = 2, "running"
        batch.save()
        self.wake.set()
        return True

    def batch_cancel(self, ident: str) -> bool:
        batch = self.batches.get(ident)
        if not batch or batch.state in ("done", "cancelled"):
            return False
        batch.state, batch.finished = "cancelled", time.time()
        for v in batch.videos:
            if v.get("task") and v["state"] in ("running", "prepared"):
                self.cancel(v["task"])
            if v["state"] in ("todo", "prepared", "running"):
                v["state"] = "cancelled" if v["state"] != "todo" else "todo"
        batch.save()
        return True

    def batch_retry(self, ident: str, path: str | None = None) -> bool:
        """Sets a video in error (or all of them) back to do; a finished batch or one awaiting translation starts again for it."""
        batch = self.batches.get(ident)
        if not batch or batch.state == "cancelled":
            return False
        targets = [v for v in batch.videos if v["state"] == "error" and path in (None, v["path"])]
        if not targets:
            return False
        for v in targets:
            v["state"], v["task"], v["error"] = "todo", None, ""
        if batch.state in ("done", "awaiting_translation"):
            batch.state, batch.finished = "running", None
        batch.save()
        self.wake.set()
        return True

    def batch_continue(self, ident: str, path: str) -> bool:
        """Takes a single video to the end of the chain, before the others. Also works on a suspended or finished batch."""
        batch = self.batches.get(ident)
        if not batch or batch.state == "cancelled":
            return False
        wants_dubbing = steps.goes_to(batch.options.get("stop"), "dubbing") and dubbing.available(config.load())
        until = batch.options["stop"] if wants_dubbing else "translation"
        with batch.lock:
            v = next((x for x in batch.videos if x["path"] == path), None)
            if v is None or v["state"] == "running":
                return False
            t = self.tasks.get(v.get("task") or "")
            if v["state"] == "prepared" and t and t.state == "paused":
                t.options.update(until=until, continued=True, pause=False)
                self.resume(t.id, batch.options.get("translator"), batch.options.get("languages"))
            else:
                t = self._batch_task(batch, v, until=until, pause=False, continued=True, check_names=True)
            v["task"], v["state"], v["started"], v["error"] = t.id, "running", time.time(), ""
            if batch.state == "done":          # starts again for this video; the page follows it like the rest of the batch
                batch.state, batch.finished, batch.phase = "running", None, 3
            batch.save()
        self.wake.set()
        return True

    def batch_goal(self, ident: str, stop: str, replace: bool | None = None) -> bool:
        """Changes what the batch must produce. Dubbing asked for afterwards: the videos already subtitled become to dub
        again, but nothing starts by itself on a suspended or finished batch ("Resume" or "Continue" episode by episode)."""
        batch = self.batches.get(ident)
        if not batch or batch.state == "cancelled" or stop not in steps.STOPS:
            return False
        dub = steps.goes_to(stop, "dubbing") and dubbing.available(config.load())
        finished_key = "muxed" if stop == "mux" else "dubbed"
        with batch.lock:
            batch.options["stop"] = stop
            if replace is not None:
                batch.options["replace"] = bool(replace) and dub
            for v in batch.videos:
                if dub and v["state"] == "done" and v.get("translated") and not v.get(finished_key):
                    v["state"] = "translated"
                elif not dub and v["state"] == "translated":
                    v["state"] = "done"
            if batch.state == "done" and any(v["state"] == "translated" for v in batch.videos):
                batch.state, batch.finished = "suspended", None
            batch.save()
        self.wake.set()
        return True

    def batch_proposals(self, ident: str, path: str | None = None) -> dict | None:
        """Names to check: those of a video, or all those of the batch."""
        batch = self.batches.get(ident)
        if not batch:
            return None
        names = None
        if path:
            f = pipeline.work_dir(batch.options.get("output_dir") or config.load()["output_dir"], path) / review.VIDEO_FILE
            try:
                names = set(json.loads(f.read_text(encoding="utf-8")).get("names", []))
            except (OSError, ValueError):
                names = set()
        return {"proposals": review.proposals(batch.file, names), "path": path or ""}

    def validate_names(self, ident: str, decisions: list[dict]) -> dict | None:
        batch = self.batches.get(ident)
        if not batch:
            return None
        accepted, rejected = review.validate(batch.file, decisions)
        with batch.lock:
            self._observe(batch)
            batch.save()
        return {"accepted": accepted, "rejected": rejected}

    def estimate_batch(self, ident: str, languages: list[str] | None = None) -> dict | None:
        """Sum of the estimates of the prepared videos (phase 1 done, task paused)."""
        batch = self.batches.get(ident)
        if not batch:
            return None
        total = None
        for v in batch.videos:
            if v["state"] != "prepared" or not v.get("task"):
                continue
            e = self.estimate(v["task"], languages if languages is not None else batch.options.get("languages"))
            if not e or not e["languages"]:              # already translated into these languages: will cost nothing
                continue
            if total is None:
                total = {**e, "videos": 0, "translators": [dict(t) for t in e["translators"]]}
            else:
                total["languages"] += [c for c in e["languages"] if c not in total["languages"]]
                for k in ("count", "requests", "input_tokens", "output_tokens"):
                    total[k] += e[k]
                for a, b in zip(total["translators"], e["translators"]):
                    for k in ("cost_usd", "duration_s"):
                        if a.get(k) is not None and b.get(k) is not None:
                            a[k] = round(a[k] + b[k], 4)
            total["videos"] += 1
        return total or {"languages": [], "count": 0}

    def _feed_batches(self):
        """Starts the next videos of each running batch, up to the number allowed at once."""
        for batch in list(self.batches.values()):
            if batch.state == "running":
                with batch.lock:
                    self._feed_batch(batch)

    def _concurrency(self, batch: Batch) -> int:
        if batch.phase != 2:
            return 1
        try:
            return translation.concurrency(config.engine(config.load(), "translators", "active_translator", batch.options.get("translator") or None))
        except config.ConfigError:
            return 1

    def _feed_batch(self, batch: Batch):
        batch.concurrency = self._concurrency(batch)
        def active(v):
            t = self.tasks.get(v.get("task") or "")
            return v["state"] == "running" and t is not None and t.state in ACTIVE_STATES
        actives = sum(1 for v in batch.videos if active(v))
        for _ in range(batch.concurrency - actives):
            v = batch.next_video()
            if v is None:
                if actives == 0:               # the parallel videos still on their way will end the phase
                    batch.end_phase()
                    batch.save()
                return
            o = batch.options
            if v["state"] == "todo":
                t = self._batch_task(batch, v, pause=batch.phase == 1 and bool(o.get("pause", True)))
                v["task"], v["state"] = t.id, "running"
                v["started"] = time.time()
            elif v["state"] == "translated":
                t = self._batch_task(batch, v, pause=False, until=o.get("stop"), review=False)
                v["task"], v["state"] = t.id, "running"
                v["started"] = time.time()
            else:
                if self.resume(v["task"], o.get("translator"), o.get("languages")) is None:
                    v["state"], v["error"] = "error", i18n.t("err.paused_task_missing")
                    continue
                v["state"], v["started"] = "running", time.time()
            actives += 1
            batch.save()

    def _batch_task(self, batch: Batch, v: dict, **options) -> Task:
        o = batch.options
        return self.add({"source": v["path"], "transcription": o.get("transcription") or "", "translator": o.get("translator") or "",
                         "language": v.get("language") or "", "languages": o.get("languages"), "output_dir": o.get("output_dir") or "",
                         "destination": o.get("destination"), "redo": bool(o.get("redo")) and v["state"] == "todo", "batch": batch.id,
                         "video_duration": v.get("duration") or 0, "glossary": str(glossary.path_for(batch.file)), "batch_file": str(batch.file),
                         "review": bool(o.get("review")), "pause": False, **options})

    def _left_to_dub(self, batch: Batch, t: Task) -> bool:
        """Translated, and the batch asks to go further: the video waits for its turn on the graphics card (phase 3)."""
        try:
            possible = dubbing.available(config.load())
        except config.ConfigError:
            possible = False
        return possible and steps.goes_to(batch.options.get("stop"), "dubbing") and not steps.goes_to(t.options.get("until"), "dubbing")

    def _update_batch(self, t: Task):
        batch = self.batches.get(t.options.get("batch") or "")
        v = batch.video_of(t.id) if batch else None
        if not v:
            return
        with batch.lock:
            if t.state == "paused":
                v["state"] = "prepared"
                if t.prepared:
                    v["count"], v["detected_language"] = len(t.prepared.cues), t.prepared.code
            elif t.state == "done":
                v["state"] = "translated" if self._left_to_dub(batch, t) else "done"
                if t.prepared:
                    v["count"], v["detected_language"] = len(t.prepared.cues), t.prepared.code
            elif t.state == "error":
                v["state"], v["error"] = "error", t.error
            elif t.state == "cancelled":
                v["state"] = "cancelled"
            if batch.state == "awaiting_translation" and not any(x["state"] == "prepared" for x in batch.videos):
                batch.end_phase()              # the last waiting video was translated or cancelled on its own
            if t.prepared:
                v["transcribed"] = True
                if (t.result or {}).get("replaced"):      # the video changed file
                    v["path"], v["name"] = t.result["mkv"].replace("\\", "/"), Path(t.result["mkv"]).name
                self._observe(batch, [v])
            batch.save()
        self.wake.set()

    def _request(self, t: Task, cfg: dict) -> pipeline.Request:
        o = t.options
        return pipeline.Request(o["source"], cfg, o.get("transcription") or None, o.get("translator") or None,
                                o.get("language") if o.get("language") is not None else None, o.get("output_dir") or None,
                                bool(o.get("redo")), on_work=lambda path: setattr(t, "work", path),
                                languages=o.get("languages"), destination=o.get("destination"),
                                on_duration=lambda s: setattr(t, "video_duration", t.video_duration or s), glossary=o.get("glossary"),
                                on_progress=lambda done, total: setattr(t, "progress", {"done": done, "total": total}))

    def _run(self, t: Task):
        t.state = "running"
        if t.started is None:
            t.started = time.time()
        o = t.options
        until = o.get("until") or "translation"
        t.pass_no, t.pass_started = 3 if steps.goes_to(until, "dubbing") else 2 if t.prepared is not None else 1, time.time()
        try:
            cfg = config.load()
            r = self._request(t, cfg)
            step = lambda name: setattr(t, "step", name)  # noqa: E731
            cancelled = lambda: t.cancel_requested  # noqa: E731
            first = t.prepared is None
            if first or glossary.fingerprint(o.get("glossary")) != t.glossary_seen:      # names validated since: segmentation to redo
                t.glossary_seen = glossary.fingerprint(o.get("glossary"))
                t.prepared = pipeline.prepare(r, t.write, step, cancelled)
            if first:
                pending = self._review(t, r, cfg, step)
                remaining = pipeline.remaining_languages(t.prepared, pipeline.target_languages(r), r.redo)
                t.estimate = estimation.estimate(cfg, len(t.prepared.cues), remaining, t.prepared.code)
                t.write(i18n.t("log.estimate") + "\n" + estimation.text(t.estimate, cfg))
                m_trans = config.engine(cfg, "translators", "active_translator", r.translator_id)
                if o.get("check_names") and pending:
                    t.state, t.step = "paused", "review"
                    t.write(i18n.t("log.pause_prefix") + " " + i18n.t("log.paused_names", n=len(pending)))
                    return
                pause = o.get("pause") if o.get("pause") is not None else estimation.pause_by_default(m_trans)
                if pause and t.estimate["languages"] and m_trans.get("type", "none") != "none":
                    t.state, t.step = "paused", "translation"
                    t.write(i18n.t("log.pause_prefix") + " " + i18n.t("log.paused_translate"))
                    return
            res = pipeline.translate_and_copy(r, t.prepared, t.write, step, cancelled)
            t.result = {"video": str(res.video), "srt": str(res.srt), "source_srt": str(res.source_srt), "language": res.language,
                        "count": res.cue_count, "translated": res.translated, "copies": [str(c) for c in res.copies],
                        "duration_s": round(res.duration_s),
                        "files": [{"language": f.language, "path": str(f.path), "count": f.count, "translated": f.translated} for f in res.files]}
            if steps.goes_to(until, "dubbing"):
                batch = self.batches.get(o.get("batch") or "")
                t.result.update(pipeline.dub_and_mux(r, t.prepared, res, until, t.write, step, cancelled, bool((batch.options if batch else o).get("replace"))))
            t.state = "done"
        except pipeline.Cancelled:
            t.state = "cancelled"
            t.write(i18n.t("log.cancelled"))
        except KNOWN_ERRORS as e:
            t.state, t.error = "error", str(e)
            t.write(i18n.t("log.error", error=e))
        except Exception as e:  # noqa: BLE001 - a task must not kill the loop
            t.state, t.error = "error", f"{type(e).__name__} : {e}"
            t.write(i18n.t("log.unexpected_error") + "\n" + traceback.format_exc())
        finally:
            if t.state in ("paused", "done"):
                self._end_of_pass(t)
            if t.state in FINISHED_STATES:
                t.finished = time.time()
                self._write_log(t)
                self._archive(t)
            if t.options.get("batch"):
                self._update_batch(t)

    def _review(self, t: Task, r: pipeline.Request, cfg: dict, step) -> list[str]:
        """AI review if the batch asks for it and it is not done yet; returns the names of this video waiting for a decision.
        An API failure does not stop the video: only the validated names are applied, and it will be redone next time."""
        o = t.options
        if not o.get("batch_file"):
            return []
        file = t.prepared.work / review.VIDEO_FILE
        if file.is_file() and not r.redo:
            try:
                return sorted(set(json.loads(file.read_text(encoding="utf-8")).get("names", [])) & review.pending_names(o["batch_file"]))
            except (OSError, ValueError):
                pass
        if not o.get("review"):
            return []
        m = review.engine(cfg, o.get("translator") or None)
        if m is None:
            t.write(i18n.t("log.review_skipped"))
            return []
        step("review")
        try:
            names = review.review(m, o["batch_file"], t.prepared.work, t.prepared.video.name, [s.text for s in t.prepared.cues], t.write, lambda: t.cancel_requested)
        except (translation.TranslationError, ValueError, KeyError) as e:
            t.write(i18n.t("log.review_failed", error=e))
            names = []
        if t.cancel_requested:
            raise pipeline.Cancelled()
        return names

    def _end_of_pass(self, t: Task):
        """Remembers the observed speed, for the time left of the batch and of the next sessions."""
        if t.options.get("continued"):         # several steps at once: says nothing about the speed of one pass
            return
        seconds, weight = time.time() - t.pass_started, t.weight()
        if seconds < timing.MIN_DURATION_S or weight <= 0:
            return
        timing.record(timing.key(t.pass_no, t.options), seconds / weight)
        batch = self.batches.get(t.options.get("batch") or "")
        if batch:
            batch.record_pass(weight, seconds)

    def _write_log(self, t: Task):
        if not t.work:
            return
        try:
            f = t.work / f"log-{time.strftime('%Y%m%d-%H%M', time.localtime(t.started or t.created))}-{t.id}.txt"
            header = ["# " + i18n.t("log.file_task", id=t.id, source=t.options.get("source")), "# " + i18n.t("log.file_options", options=json.dumps(t.options, ensure_ascii=False)),
                      "# " + i18n.t("log.file_state", state=t.state) + (f" ({t.error})" if t.error else "")]
            f.write_text("\n".join(header + t.log) + "\n", encoding="utf-8")
            t.log_file = str(f)
        except OSError as e:
            t.write(i18n.t("log.journal_not_saved", error=e))

    # --- History ---
    def _load_history(self) -> list[dict]:
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _archive(self, t: Task):
        with self.lock:
            self.history = [h for h in self.history if h["id"] != t.id]
            self.history.insert(0, t.summary())
            del self.history[50:]
            try:
                HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
                HISTORY_FILE.write_text(json.dumps(self.history, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass
