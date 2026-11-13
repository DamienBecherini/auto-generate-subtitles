"""Batches of videos: creation, resume from the batch file, actions (suspend, translate, continue, goal…), and the
feeding of the task queue with their videos, phase by phase (see batches.py).

The manager listens to the queue (`tasks.QueueEvents`): each change of a task updates its video and the batch file.
"""
import json
import subprocess
import threading
import time
from pathlib import Path

from .. import config, dubbing, folder, glossary, i18n, language, pipeline, review, source, steps, translation
from ..states import ACTIVE_TASK_STATES, FINISHED_BATCH_STATES, BatchState, TaskState, VideoState
from .batches import Batch
from .tasks import Task, TaskQueue

LAST_BATCH_FILE = config.user_dir() / "last_batch.json"   # path of the file of the last unfinished batch


class BatchManager:
    def __init__(self, queue: TaskQueue):
        self.queue = queue
        self.all: dict[str, Batch] = {}
        self.lock = threading.Lock()
        queue.events = self

    # --- creation and resume ---
    def create(self, root: str, videos: list[dict], options: dict) -> Batch:
        cfg = config.load()
        dest = {**pipeline.DEFAULT_DESTINATION, **(cfg.get("destination") or {}), **(options.get("destination") or {})}
        file = folder.batch_path(Path(root), Path(options.get("output_dir") or cfg["output_dir"]), bool(dest.get("output_dir")))
        items = [{"path": v["path"], "name": Path(v["path"]).name, "language": v.get("language") or "", "state": VideoState.TODO,
                  "task": None, "count": None, "detected_language": "", "error": ""} for v in videos]
        return self._open(Batch(root, items, options, file))

    def load(self, file: str) -> Batch:
        batch = Batch.from_file(Path(file))
        for other in list(self.all.values()):                  # a single batch per file
            if other.file == batch.file and other.state not in FINISHED_BATCH_STATES:
                self.cancel(other.id)                          # outside the lock: cancelling a task archives it, which takes it
        return self._open(batch)

    def _open(self, batch: Batch) -> Batch:
        batch.after_save, batch.step_of = self._remember_last, self._task_step
        with self.lock:
            self.all[batch.id] = batch
        self.mark_transcribed(batch)
        batch.save()
        self._measure_durations(batch)
        self.queue.wake.set()
        return batch

    @classmethod
    def mark_transcribed(cls, batch: Batch):
        """Videos whose transcription is already there: `Batch.next_video` takes them before those to transcribe."""
        output_dir = batch.options.get("output_dir") or config.load()["output_dir"]
        for v in batch.videos:
            v["transcribed"] = not batch.options.get("redo") and pipeline.transcription_kept(output_dir, v["path"])
        cls.observe(batch)

    @staticmethod
    def observe(batch: Batch, videos: list[dict] | None = None):
        """Steps done per video, read from the disk: dots of the list and choice of "Continue"."""
        cfg = config.load()
        output_dir = batch.options.get("output_dir") or cfg["output_dir"]
        languages = language.normalize_codes(batch.options.get("languages") or cfg.get("target_languages", ["fr"]))
        pending = review.pending_names(batch.file)
        for v in videos if videos is not None else batch.videos:
            done = steps.observe(pipeline.work_dir(output_dir, v["path"]), v["path"], languages, v.get("detected_language") or v.get("language") or "", pending)
            done.pop("transcribed")            # computed by mark_transcribed (takes "redo" into account) or at the end of the task
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

    # --- last batch: offered for resuming at the start of the next session ---
    def _remember_last(self, batch: Batch):
        try:
            if batch.state not in FINISHED_BATCH_STATES:
                LAST_BATCH_FILE.parent.mkdir(parents=True, exist_ok=True)
                LAST_BATCH_FILE.write_text(json.dumps({"file": str(batch.file)}, ensure_ascii=False), encoding="utf-8")
            elif self._last_file() == str(batch.file):
                LAST_BATCH_FILE.unlink(missing_ok=True)
        except OSError:
            pass

    @staticmethod
    def _last_file() -> str:
        try:
            return json.loads(LAST_BATCH_FILE.read_text(encoding="utf-8")).get("file", "")
        except (OSError, ValueError):
            return ""

    def last(self) -> dict | None:
        """Batch left unfinished at the end of the previous session, or None (finished, already open, or file gone)."""
        file = self._last_file()
        if not file:
            return None
        try:
            d = folder.read_batch(Path(file))
        except source.SourceError:
            return None
        videos = d["videos"]
        if d.get("state") in FINISHED_BATCH_STATES or d.get("id") in self.all or all(v.get("state") == VideoState.DONE for v in videos):
            return None
        return {"file": d["file"], "root": d.get("root", ""), "state": d.get("state", ""), "updated": d.get("updated", ""),
                "total": len(videos), "done": sum(1 for v in videos if v.get("state") == VideoState.DONE),
                "translator": (d.get("options") or {}).get("translator", "")}

    def forget_last(self):
        """"Ignore": the batch is no longer offered; its file stays, for "Resume a batch…"."""
        LAST_BATCH_FILE.unlink(missing_ok=True)

    def _task_step(self, ident: str | None) -> str:
        t = self.queue.get(ident or "")
        return t.step if t and t.state == TaskState.RUNNING else ""

    # --- queries ---
    def get(self, ident: str) -> Batch | None:
        return self.all.get(ident)

    def active(self) -> Batch | None:
        return next((b for b in reversed(list(self.all.values())) if b.state not in FINISHED_BATCH_STATES), None)

    def running(self) -> Batch | None:
        return next((b for b in list(self.all.values()) if b.state == BatchState.RUNNING), None)

    def summaries(self) -> list[dict]:
        return [b.summary() for b in reversed(list(self.all.values()))]

    def proposals(self, ident: str, path: str | None = None) -> dict | None:
        """Names to check: those of a video, or all those of the batch."""
        batch = self.all.get(ident)
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

    def estimate(self, ident: str, languages: list[str] | None = None) -> dict | None:
        """Sum of the estimates of the prepared videos (phase 1 done, task paused)."""
        batch = self.all.get(ident)
        if not batch:
            return None
        total = None
        for v in batch.videos:
            if v["state"] != VideoState.PREPARED or not v.get("task"):
                continue
            e = self.queue.estimate(v["task"], languages if languages is not None else batch.options.get("languages"))
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

    # --- actions (False: not possible in the current state) ---
    def suspend(self, ident: str) -> bool:
        batch = self.all.get(ident)
        if not batch or batch.state != BatchState.RUNNING:
            return False
        batch.state = BatchState.SUSPENDED                     # the running video goes on to its end (or its pause)
        batch.save()
        return True

    def stop(self, ident: str) -> bool:
        """Pause now: the running videos are interrupted (the transcription server is told by the closed connection,
        the translation stops between two chunks) and go back to the list; what they finished is kept on the disk."""
        batch = self.all.get(ident)
        if not batch or batch.state != BatchState.RUNNING:
            return False
        batch.state = BatchState.SUSPENDED
        batch.save()
        for v in batch.videos:
            t = self.queue.get(v.get("task") or "") if v["state"] == VideoState.RUNNING else None
            if t:
                t.options["stopped"] = True        # task_changed puts the video back instead of marking it cancelled
                self.queue.cancel(t.id)
        return True

    def resume(self, ident: str, changes: dict | None = None) -> bool:
        """Starts a suspended batch again; `changes` (translator, transcription, languages, pause) apply to the videos not started yet."""
        batch = self.all.get(ident)
        if not batch or batch.state != BatchState.SUSPENDED:
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
        batch.state = BatchState.RUNNING
        batch.save()
        if changes.get("transcription"):
            self._move_transcriptions(batch, changes["transcription"])
        self.queue.wake.set()
        return True

    def _move_transcriptions(self, batch: Batch, engine: str):
        """After Pause, the running video goes on to its end: if it is still being transcribed by another engine than
        the one just chosen, it is stopped (back in the list) and starts again on the chosen one."""
        for v in batch.videos:
            t = self.queue.get(v.get("task") or "") if v["state"] == VideoState.RUNNING else None
            if t and t.state in ACTIVE_TASK_STATES and t.prepared is None and t.pass_no == 1 \
                    and (t.options.get("transcription") or "") != engine:
                t.options["stopped"] = True
                self.queue.cancel(t.id)

    def translate(self, ident: str, translator: str | None, languages: list[str] | None, stop: str | None = None) -> bool:
        batch = self.all.get(ident)
        if not batch or batch.state != BatchState.AWAITING_TRANSLATION:
            return False
        if translator is not None:
            batch.options["translator"] = translator
        if languages is not None:
            batch.options["languages"] = language.normalize_codes(languages)
        if stop:
            batch.options["stop"] = stop
        batch.phase, batch.state = 2, BatchState.RUNNING
        batch.save()
        self.queue.wake.set()
        return True

    def cancel(self, ident: str) -> bool:
        batch = self.all.get(ident)
        if not batch or batch.state in FINISHED_BATCH_STATES:
            return False
        batch.state, batch.finished = BatchState.CANCELLED, time.time()
        for v in batch.videos:
            if v.get("task") and v["state"] in (VideoState.RUNNING, VideoState.PREPARED):
                self.queue.cancel(v["task"])
            if v["state"] in (VideoState.PREPARED, VideoState.RUNNING):
                v["state"] = VideoState.CANCELLED
        batch.save()
        return True

    def retry(self, ident: str, path: str | None = None) -> bool:
        """Sets a video in error (or all of them) back to do; a finished batch or one awaiting translation starts again for it."""
        batch = self.all.get(ident)
        if not batch or batch.state == BatchState.CANCELLED:
            return False
        targets = [v for v in batch.videos if v["state"] == VideoState.ERROR and path in (None, v["path"])]
        if not targets:
            return False
        for v in targets:
            v["state"], v["task"], v["error"] = VideoState.TODO, None, ""
        if batch.state in (BatchState.DONE, BatchState.AWAITING_TRANSLATION):
            batch.state, batch.finished = BatchState.RUNNING, None
        batch.save()
        self.queue.wake.set()
        return True

    def continue_video(self, ident: str, path: str) -> bool:
        """Takes a single video to the end of the chain, before the others. Also works on a suspended or finished batch."""
        batch = self.all.get(ident)
        if not batch or batch.state == BatchState.CANCELLED:
            return False
        wants_dubbing = steps.goes_to(batch.options.get("stop"), "dubbing") and dubbing.available(config.load())
        until = batch.options["stop"] if wants_dubbing else "translation"
        with batch.lock:
            v = next((x for x in batch.videos if x["path"] == path), None)
            if v is None or v["state"] == VideoState.RUNNING:
                return False
            t = self.queue.get(v.get("task") or "")
            if v["state"] == VideoState.PREPARED and t and t.state == TaskState.PAUSED:
                t.options.update(until=until, continued=True, pause=False)
                self.queue.resume(t.id, batch.options.get("translator"), batch.options.get("languages"))
            else:
                t = self._add_task(batch, v, until=until, pause=False, continued=True, check_names=True)
            v["task"], v["state"], v["started"], v["error"] = t.id, VideoState.RUNNING, time.time(), ""
            if batch.state == BatchState.DONE:          # starts again for this video; the page follows it like the rest of the batch
                batch.state, batch.finished, batch.phase = BatchState.RUNNING, None, 3
            batch.save()
        self.queue.wake.set()
        return True

    def goal(self, ident: str, stop: str, replace: bool | None = None) -> bool:
        """Changes what the batch must produce. Dubbing asked for afterwards: the videos already subtitled become to dub
        again, but nothing starts by itself on a suspended or finished batch ("Resume" or "Continue" episode by episode)."""
        batch = self.all.get(ident)
        if not batch or batch.state == BatchState.CANCELLED or stop not in steps.STOPS:
            return False
        dub = steps.goes_to(stop, "dubbing") and dubbing.available(config.load())
        finished_key = "muxed" if stop == "mux" else "dubbed"
        with batch.lock:
            batch.options["stop"] = stop
            if replace is not None:
                batch.options["replace"] = bool(replace) and dub
            for v in batch.videos:
                if dub and v["state"] == VideoState.DONE and v.get("translated") and not v.get(finished_key):
                    v["state"] = VideoState.TRANSLATED
                elif not dub and v["state"] == VideoState.TRANSLATED:
                    v["state"] = VideoState.DONE
            if batch.state == BatchState.DONE and any(v["state"] == VideoState.TRANSLATED for v in batch.videos):
                batch.state, batch.finished = BatchState.SUSPENDED, None
            batch.save()
        self.queue.wake.set()
        return True

    def validate_names(self, ident: str, decisions: list[dict]) -> dict | None:
        batch = self.all.get(ident)
        if not batch:
            return None
        accepted, rejected = review.validate(batch.file, decisions)
        with batch.lock:
            self.observe(batch)
            batch.save()
        return {"accepted": accepted, "rejected": rejected}

    # --- feeding the queue ---
    def feed(self):
        """Starts the next videos of each running batch, up to the number allowed at once."""
        for batch in list(self.all.values()):
            if batch.state == BatchState.RUNNING:
                with batch.lock:
                    self._feed(batch)

    def _concurrency(self, batch: Batch) -> int:
        if batch.phase != 2:
            return 1
        try:
            return translation.concurrency(config.engine(config.load(), "translators", "active_translator", batch.options.get("translator") or None))
        except config.ConfigError:
            return 1

    def _feed(self, batch: Batch):
        batch.concurrency = self._concurrency(batch)

        def active(v):
            t = self.queue.get(v.get("task") or "")
            return v["state"] == VideoState.RUNNING and t is not None and t.state in ACTIVE_TASK_STATES
        actives = sum(1 for v in batch.videos if active(v))
        for _ in range(batch.concurrency - actives):
            v = batch.next_video()
            if v is None:
                if actives == 0:               # the parallel videos still on their way will end the phase
                    batch.end_phase()
                    batch.save()
                return
            o = batch.options
            if v["state"] == VideoState.TODO:
                t = self._add_task(batch, v, pause=batch.phase == 1 and bool(o.get("pause", True)))
            elif v["state"] == VideoState.TRANSLATED:
                t = self._add_task(batch, v, pause=False, until=o.get("stop"), review=False)
            else:
                t = self.queue.resume(v["task"], o.get("translator"), o.get("languages"))
                if t is None:
                    v["state"], v["error"] = VideoState.ERROR, i18n.t("err.paused_task_missing")
                    continue
            v["task"], v["state"], v["started"] = t.id, VideoState.RUNNING, time.time()
            actives += 1
            batch.save()

    def _add_task(self, batch: Batch, v: dict, **options) -> Task:
        o = batch.options
        return self.queue.add({"source": v["path"], "transcription": o.get("transcription") or "", "translator": o.get("translator") or "",
                               "language": v.get("language") or "", "languages": o.get("languages"), "output_dir": o.get("output_dir") or "",
                               "destination": o.get("destination"), "redo": bool(o.get("redo")) and v["state"] == VideoState.TODO, "batch": batch.id,
                               "video_duration": v.get("duration") or 0, "glossary": str(glossary.path_for(batch.file)), "batch_file": str(batch.file),
                               "review": bool(o.get("review")), "pause": False, **options})

    def _left_to_dub(self, batch: Batch, t: Task) -> bool:
        """Translated, and the batch asks to go further: the video waits for its turn on the graphics card (phase 3)."""
        try:
            possible = dubbing.available(config.load())
        except config.ConfigError:
            possible = False
        return possible and steps.goes_to(batch.options.get("stop"), "dubbing") and not steps.goes_to(t.options.get("until"), "dubbing")

    def _batch_of(self, t: Task) -> Batch | None:
        return self.all.get(t.options.get("batch") or "")

    # --- events of the queue (tasks.QueueEvents) ---
    def runs_in_parallel(self, t: Task) -> bool:
        """Translations of a batch by a remote API; never the dubbing (a single graphics card)."""
        batch = self._batch_of(t)
        return bool(batch and batch.concurrency > 1 and t.prepared is not None and not steps.goes_to(t.options.get("until"), "dubbing"))

    def replaces_original(self, t: Task) -> bool:
        batch = self._batch_of(t)
        return bool((batch.options if batch else t.options).get("replace"))

    def pass_measured(self, t: Task, weight: float, seconds: float):
        batch = self._batch_of(t)
        if batch:
            batch.record_pass(weight, seconds)

    def tick(self):
        self.feed()

    def task_changed(self, t: Task):
        batch = self._batch_of(t)
        v = batch.video_of(t.id) if batch else None
        if not v:
            return
        with batch.lock:
            if t.state == TaskState.PAUSED:
                v["state"] = VideoState.PREPARED
            elif t.state == TaskState.DONE:
                v["state"] = VideoState.TRANSLATED if self._left_to_dub(batch, t) else VideoState.DONE
            elif t.state == TaskState.ERROR:
                v["state"], v["error"] = VideoState.ERROR, t.error
            elif t.state == TaskState.CANCELLED and t.options.get("stopped"):
                # "Stop": back in the list; the dubbing pass waits for its turn again, the rest starts again from the
                # transcription, which is kept (and so are the translations and the separated voice)
                v["state"] = VideoState.TRANSLATED if t.pass_no == 3 else VideoState.TODO
                v["task"], v["error"] = None, ""
            elif t.state == TaskState.CANCELLED:
                v["state"] = VideoState.CANCELLED
            if t.prepared and t.state in (TaskState.PAUSED, TaskState.DONE):
                v["count"], v["detected_language"] = len(t.prepared.cues), t.prepared.code
            if batch.state == BatchState.AWAITING_TRANSLATION and not any(x["state"] == VideoState.PREPARED for x in batch.videos):
                batch.end_phase()              # the last waiting video was translated or cancelled on its own
            if t.prepared:
                v["transcribed"] = True
                if (t.result or {}).get("replaced"):      # the video changed file
                    v["path"], v["name"] = t.result["mkv"].replace("\\", "/"), Path(t.result["mkv"]).name
                self.observe(batch, [v])
            batch.save()
        self.queue.wake.set()
