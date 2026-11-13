"""Batch of videos (source = a folder): the queue chains one task per video, phase 1 for all of them (subtitles in
the spoken language), then, after "Translate all", phase 2 for each one. The batch file (`subtitles.batch.json`, in
the output folder if the "output folder" destination is ticked, otherwise in the source folder) is rewritten at each
change: selection, language per video, progress. After an interruption, "Resume a batch" reads it back and only redoes
what is missing (the "done" videos are skipped; the others start again from phase 1, fast thanks to the kept
transcription.json)."""
import threading
import time
import uuid
from pathlib import Path

from .. import folder, i18n, steps
from ..states import BatchState, VideoState
from . import timing

WANTED = {1: VideoState.TODO, 2: VideoState.PREPARED, 3: VideoState.TRANSLATED}   # state of the videos each phase takes care of


class Batch:
    def __init__(self, root: str, videos: list[dict], options: dict, file: Path, ident: str | None = None):
        self.id = ident or uuid.uuid4().hex[:8]
        self.root = root
        self.videos = videos                   # [{path, name, language, state, task, count, detected_language, error}]
        self.options = options                 # transcription, translator, languages, destination, output_dir, pause, stop, review
        self.file = Path(file)
        self.state = BatchState.RUNNING
        self.phase = 1
        self.created = time.time()
        self.finished = None
        self.after_save = None                 # manager callback (remembers the last batch for the next session)
        self.step_of = None                    # manager callback: current step of a task (id → "transcription"…)
        self.passes: dict[int, list[tuple[float, float]]] = {}   # phase → [(weight, seconds)] of the processed videos
        self.concurrency = 1                   # videos processed at once (several: translation by a remote API)
        self.lock = threading.RLock()          # the parallel tasks update the batch and rewrite its file

    # --- serialisation ---
    def to_dict(self) -> dict:
        return {"id": self.id, "root": self.root, "options": self.options, "state": self.state, "phase": self.phase,
                "created": self.created, "finished": self.finished, "file": str(self.file).replace("\\", "/"), "videos": self.videos}

    def summary(self) -> dict:
        counts = {e: sum(1 for v in self.videos if v["state"] == e) for e in VideoState}
        measures = self.passes.get(self.phase, [])[-10:]
        current = self.current()
        # Without a pause, a video of phase 1 goes up to the translation: the phase alone does not say where it is.
        step = (self.step_of(current.get("task")) or "") if current and self.step_of else ""
        return {**self.to_dict(), "counts": counts, "step": step, "total": len(self.videos), "concurrency": self.concurrency,
                "avg_duration_s": round(sum(s for _, s in measures) / len(measures)) if measures else None,
                "current": current["path"] if current else None,
                "remaining_s": self.remaining_s(timing.known_rate(self.phase, self.options) if self.state == BatchState.RUNNING else None),
                "dubbing_s": self.dubbing_left_s()}

    def save(self):
        with self.lock:
            try:
                folder.write_batch(self.file, self.to_dict())
            except OSError:
                pass
            if self.after_save:
                self.after_save(self)

    # --- progress ---
    def next_video(self) -> dict | None:
        wanted = WANTED[self.phase]
        # In phase 2, a video set back to do ("Retry") starts again from the transcription, without a pause.
        to_process = [v for v in self.videos if v["state"] == wanted] or [v for v in self.videos if v["state"] == VideoState.TODO]
        # Resume: the transcriptions already done go first (a few seconds each), the counter is quickly right.
        return next((v for v in to_process if v["state"] == VideoState.TODO and v.get("transcribed")), to_process[0] if to_process else None)

    def current(self) -> dict | None:
        return next((v for v in self.videos if v["state"] == VideoState.RUNNING), None)

    def weight(self, v: dict) -> float:
        """What makes the duration of a video vary in this phase (see timing.py); 0 if still unknown."""
        if self.phase in (1, 3):
            return float(v.get("duration") or 0)
        return float(v.get("count") or 0) * max(1, len(self.options.get("languages") or []))

    def record_pass(self, weight: float, seconds: float):
        self.passes.setdefault(self.phase, []).append((weight, seconds))

    def remaining_s(self, known_rate: float | None = None, now: float | None = None) -> int | None:
        """Seconds before the end of the current phase, from the last 10 processed videos, otherwise `known_rate`.
        None if the batch is not working or if nothing allows an estimate."""
        if self.state != BatchState.RUNNING:
            return None
        wanted = WANTED[self.phase]
        to_transcribe = lambda v: self.phase != 1 or not v.get("transcribed")  # noqa: E731 - a resume costs nothing
        upcoming = [v for v in self.videos if v["state"] == wanted and to_transcribe(v)]
        running = [v for v in self.videos if v["state"] == VideoState.RUNNING and to_transcribe(v)]
        measures = self.passes.get(self.phase, [])[-10:]
        measured_weight = sum(p for p, _ in measures)
        rate = sum(s for _, s in measures) / measured_weight if measured_weight > 0 else known_rate
        known = [p for p in map(self.weight, upcoming + running) if p > 0]
        if not rate or not known:
            return None
        mean = sum(known) / len(known)
        left = sum(self.weight(v) or mean for v in upcoming) * rate
        for v in running:
            elapsed = (time.time() if now is None else now) - v.get("started", time.time())
            left += max(0.0, (self.weight(v) or mean) * rate - elapsed)
        return round(left / max(1, self.concurrency))

    def progress_text(self) -> str:
        """Short text for the Local service module: "batch 3/22", running video included."""
        wanted = WANTED[self.phase]
        total = len(self.videos)
        rank = max(1, total - sum(1 for v in self.videos if v["state"] == wanted))
        return i18n.t("activity.batch", rank=rank, total=total) + {1: "", 2: " (" + i18n.t("step.translation").lower() + ")", 3: " (" + i18n.t("step.dubbing").lower() + ")"}[self.phase]

    def dubbing_left_s(self) -> int:
        """Graphics card time for the videos still to dub; 0 if this batch only wants subtitles."""
        if not steps.goes_to(self.options.get("stop"), "dubbing"):
            return 0
        todo = [v for v in self.videos if not v.get("muxed") and v["state"] not in (VideoState.CANCELLED, VideoState.ERROR)]
        return round(sum(float(v.get("duration") or 0) for v in todo) * (timing.known_rate(3, self.options) or 0))

    def video_of(self, task_id: str) -> dict | None:
        return next((v for v in self.videos if v.get("task") == task_id), None)

    def end_phase(self):
        """Nothing more to start in the current phase."""
        if self.phase == 1 and any(v["state"] == VideoState.PREPARED for v in self.videos):
            self.state = BatchState.AWAITING_TRANSLATION
        elif self.phase < 3 and any(v["state"] == VideoState.TRANSLATED for v in self.videos):
            self.phase, self.state = 3, BatchState.RUNNING
        else:
            self.state, self.finished = BatchState.DONE, time.time()

    @classmethod
    def from_file(cls, file: Path) -> "Batch":
        """Batch read back for a resume: the unfinished videos start again from phase 1 (fast: transcription kept)."""
        d = folder.read_batch(file)
        videos = []
        for v in d["videos"]:
            state = VideoState(v["state"]) if v.get("state") in (VideoState.DONE, VideoState.TRANSLATED) else VideoState.TODO   # translated: only the dubbing is left
            videos.append({**v, "state": state, "task": None if state != VideoState.DONE else v.get("task"), "error": ""})
        batch = cls(d["root"], videos, d.get("options", {}), Path(d["file"]), d.get("id"))
        batch.state = BatchState.SUSPENDED
        batch.created = d.get("created") or time.time()
        return batch
