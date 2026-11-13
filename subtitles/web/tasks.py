"""Queue of subtitling tasks: one at a time (the transcription server and the local AI only handle one), except the
translation of a batch by a remote API, which moves forward on several videos (`translation.concurrency`).

Two phases (see pipeline): preparation (source, audio, transcription, original subtitles), then translation and
copies. With the `pause` option, the task stops between both (state `paused`) with a cost estimate; `resume()`
starts the second phase, possibly with another translator or other languages.
History (last 50 tasks, summaries) in %LOCALAPPDATA%/subtitles/history.json; the full log of each task is written
at its end in the working folder of the video (.work/<name>/log-<date>-<id>.txt) and read back from there for the
tasks of previous sessions.

The queue knows nothing of batches: it tells what happens to its tasks to a `QueueEvents` (the batch manager).
"""
import json
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Protocol

from .. import config, dubbing, estimation, glossary, i18n, language, local_transcription, pipeline, review, source, steps, transcription, translation
from ..states import ACTIVE_TASK_STATES, FINISHED_TASK_STATES, TaskState
from . import timing

HISTORY_FILE = config.user_dir() / "history.json"
HISTORY_SIZE = 50
KNOWN_ERRORS = (source.SourceError, transcription.TranscriptionError, translation.TranslationError,
                config.ConfigError, local_transcription.LocalServerError, dubbing.DubbingError)


class Task:
    def __init__(self, options: dict):
        self.id = uuid.uuid4().hex[:8]
        self.options = options                 # source, transcription, translator, language, languages, output_dir, destination, redo, pause
        self.state = TaskState.QUEUED
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
        if self.state != TaskState.RUNNING or self.pass_started is None or self.weight() <= 0:
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
                "duration_s": None if self.state in ACTIVE_TASK_STATES else timing.log_duration(self.log),
                "prepared": {"video": str(self.prepared.video), "language": self.prepared.code, "count": len(self.prepared.cues),
                             "source_srt": str(self.prepared.source_srt)} if self.prepared else None,
                "options": {k: v for k, v in self.options.items() if k != "source"}}

    def detail(self, since: int = 0) -> dict:
        d = self.summary()
        d["log"] = self.log[since:]
        d["log_total"] = len(self.log)
        return d


class QueueEvents(Protocol):
    """What the queue tells (Observer): implemented by the batch manager, which gets its videos moving from it."""

    def runs_in_parallel(self, t: Task) -> bool: ...          # its own thread instead of waiting for its turn
    def replaces_original(self, t: Task) -> bool: ...         # the MKV may replace the video (setting of the batch)
    def task_changed(self, t: Task) -> None: ...              # end of a pass, pause, cancellation
    def pass_measured(self, t: Task, weight: float, seconds: float) -> None: ...
    def tick(self) -> None: ...                               # each turn of the loop: start what can start


class NoEvents:
    """Queue alone (tests, single videos): nobody listens."""

    def runs_in_parallel(self, t: Task) -> bool:
        return False

    def replaces_original(self, t: Task) -> bool:
        return bool(t.options.get("replace"))

    def task_changed(self, t: Task) -> None:
        pass

    def pass_measured(self, t: Task, weight: float, seconds: float) -> None:
        pass

    def tick(self) -> None:
        pass


class TaskQueue:
    def __init__(self, history: list[dict] | None = None):
        self.tasks: dict[str, Task] = {}
        self.order: list[str] = []
        self.lock = threading.Lock()
        self.wake = threading.Event()
        self.history = self._load_history() if history is None else history
        self.events: QueueEvents = NoEvents()
        self._durations: dict[tuple, int | None] = {}

    def start(self):
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
        if not r or r["state"] != TaskState.ERROR or (r.get("options") or {}).get("batch"):
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
        if not t or t.state not in (*ACTIVE_TASK_STATES, TaskState.PAUSED):
            return False
        t.cancel_requested = True
        if t.state in (TaskState.QUEUED, TaskState.PAUSED):
            t.state, t.finished = TaskState.CANCELLED, time.time()
            t.write(i18n.t("log.cancelled_before_start") if t.prepared is None else i18n.t("log.cancelled_in_pause"))
            self._write_log(t)
            self._archive(t)
            self.events.task_changed(t)
        else:
            t.write(i18n.t("log.cancel_requested"))
        return True

    def resume(self, ident: str, translator: str | None = None, languages: list[str] | None = None) -> Task | None:
        """Second phase of a paused task, possibly with another translator or other languages."""
        t = self.tasks.get(ident)
        if not t or t.state != TaskState.PAUSED:
            return None
        if translator is not None:
            t.options["translator"] = translator
        if languages is not None:
            t.options["languages"] = language.normalize_codes(languages)
        t.state = TaskState.QUEUED
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
        wanted = language.normalize_codes(languages) if languages is not None else pipeline.target_languages(self.request(t, cfg))
        remaining = pipeline.remaining_languages(t.prepared, wanted, bool(t.options.get("redo")))
        return estimation.estimate(cfg, len(t.prepared.cues), remaining, t.prepared.code)

    def current(self) -> Task | None:
        return next((t for t in list(self.tasks.values()) if t.state in ACTIVE_TASK_STATES), None)

    def paused(self) -> list[Task]:
        return [self.tasks[i] for i in list(self.order) if self.tasks[i].state == TaskState.PAUSED]

    def all_tasks(self) -> list[dict]:
        current = [self.tasks[i].summary() for i in reversed(list(self.order))]
        seen = {t["id"] for t in current}
        return current + [{**h, "duration_s": self._archived_duration(h)} for h in self.history if h["id"] not in seen]

    def close(self):
        """When the service stops: the paused single tasks are archived with their log. Those of a batch stay
        described by the batch file, from which it is resumed. In both cases transcription.json avoids transcribing again."""
        for t in self.paused():
            if not t.options.get("batch"):
                t.write(i18n.t("log.stopped_in_pause"))
                t.state, t.finished = TaskState.CANCELLED, time.time()
                self._write_log(t)
                self._archive(t)

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
                if t.state == TaskState.QUEUED:
                    self._start(t)
            self.events.tick()

    def _start(self, t: Task):
        """One task at a time, except those the batch manager lets run in parallel: each one in its thread."""
        if self.events.runs_in_parallel(t):
            t.state = TaskState.RUNNING                    # before the thread: the loop must not start it again
            threading.Thread(target=self._run, args=(t,), daemon=True, name=f"task-{t.id}").start()
        else:
            self._run(t)

    def request(self, t: Task, cfg: dict) -> pipeline.Request:
        o = t.options
        return pipeline.Request(o["source"], cfg, o.get("transcription") or None, o.get("translator") or None,
                                o.get("language") if o.get("language") is not None else None, o.get("output_dir") or None,
                                bool(o.get("redo")), on_work=lambda path: setattr(t, "work", path),
                                languages=o.get("languages"), destination=o.get("destination"),
                                on_duration=lambda s: setattr(t, "video_duration", t.video_duration or s), glossary=o.get("glossary"),
                                on_progress=lambda done, total: setattr(t, "progress", {"done": done, "total": total}))

    def _run(self, t: Task):
        t.state = TaskState.RUNNING
        if t.started is None:
            t.started = time.time()
        o = t.options
        until = o.get("until") or "translation"
        t.pass_no, t.pass_started = 3 if steps.goes_to(until, "dubbing") else 2 if t.prepared is not None else 1, time.time()
        try:
            cfg = config.load()
            r = self.request(t, cfg)
            step = lambda name: setattr(t, "step", name)  # noqa: E731
            cancelled = lambda: t.cancel_requested  # noqa: E731
            first = t.prepared is None
            if first or glossary.fingerprint(o.get("glossary")) != t.glossary_seen:      # names validated since: segmentation to redo
                t.glossary_seen = glossary.fingerprint(o.get("glossary"))
                t.prepared = pipeline.prepare(r, t.write, step, cancelled)
            if first and self._pauses_after_preparation(t, r, cfg, step):
                return
            res = pipeline.translate_and_copy(r, t.prepared, t.write, step, cancelled)
            t.result = {"video": str(res.video), "srt": str(res.srt), "source_srt": str(res.source_srt), "language": res.language,
                        "count": res.cue_count, "translated": res.translated, "copies": [str(c) for c in res.copies],
                        "duration_s": round(res.duration_s),
                        "files": [{"language": f.language, "path": str(f.path), "count": f.count, "translated": f.translated} for f in res.files]}
            if steps.goes_to(until, "dubbing"):
                t.result.update(pipeline.dub_and_mux(r, t.prepared, res, until, t.write, step, cancelled, self.events.replaces_original(t)))
            t.state = TaskState.DONE
        except pipeline.Cancelled:
            t.state = TaskState.CANCELLED
            t.write(i18n.t("log.cancelled"))
        except KNOWN_ERRORS as e:
            t.state, t.error = TaskState.ERROR, str(e)
            t.write(i18n.t("log.error", error=e))
        except Exception as e:  # noqa: BLE001 - a task must not kill the loop
            t.state, t.error = TaskState.ERROR, f"{type(e).__name__} : {e}"
            t.write(i18n.t("log.unexpected_error") + "\n" + traceback.format_exc())
        finally:
            if t.state in (TaskState.PAUSED, TaskState.DONE):
                self._end_of_pass(t)
            if t.state in FINISHED_TASK_STATES:
                t.finished = time.time()
                self._write_log(t)
                self._archive(t)
            self.events.task_changed(t)

    def _pauses_after_preparation(self, t: Task, r: pipeline.Request, cfg: dict, step) -> bool:
        """After the first phase: AI review, estimate, then pause if names wait for a decision or if the translation costs."""
        o = t.options
        pending = self._review(t, r, cfg, step)
        remaining = pipeline.remaining_languages(t.prepared, pipeline.target_languages(r), r.redo)
        t.estimate = estimation.estimate(cfg, len(t.prepared.cues), remaining, t.prepared.code)
        t.write(i18n.t("log.estimate") + "\n" + estimation.text(t.estimate, cfg))
        m_trans = config.engine(cfg, "translators", "active_translator", r.translator_id)
        if o.get("check_names") and pending:
            t.state, t.step = TaskState.PAUSED, "review"
            t.write(i18n.t("log.pause_prefix") + " " + i18n.t("log.paused_names", n=len(pending)))
            return True
        pause = o.get("pause") if o.get("pause") is not None else estimation.pause_by_default(m_trans)
        if pause and t.estimate["languages"] and m_trans.get("type", "none") != "none":
            t.state, t.step = TaskState.PAUSED, "translation"
            t.write(i18n.t("log.pause_prefix") + " " + i18n.t("log.paused_translate"))
            return True
        return False

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
        self.events.pass_measured(t, weight, seconds)

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
    @staticmethod
    def _load_history() -> list[dict]:
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []

    def _archive(self, t: Task):
        with self.lock:
            self.history = [h for h in self.history if h["id"] != t.id]
            self.history.insert(0, t.summary())
            del self.history[HISTORY_SIZE:]
            try:
                HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
                HISTORY_FILE.write_text(json.dumps(self.history, ensure_ascii=False, indent=1), encoding="utf-8")
            except OSError:
                pass
