"""Translation of a batch by a remote API: several videos at once, never more than allowed."""
import threading
from types import SimpleNamespace

from subtitles import config, translation
from subtitles.web import tasks
from subtitles.web.batches import Batch
from subtitles.web.tasks import Manager, Task

CLAUDE = {"id": "claude", "name": "Claude", "type": "claude"}
LOCAL = {"id": "local", "name": "Local", "type": "openai"}


def manager(monkeypatch, tmp_path, translator):
    monkeypatch.setattr(tasks, "LAST_BATCH_FILE", tmp_path / "last_batch.json")
    monkeypatch.setattr(config, "load", lambda: {"translators": [CLAUDE, LOCAL, {**CLAUDE, "id": "two", "parallel": 2}],
                                                 "active_translator": translator, "output_dir": str(tmp_path)})
    g = Manager.__new__(Manager)
    g.tasks, g.order, g.batches, g.history = {}, [], {}, []
    g.lock, g.wake = threading.Lock(), threading.Event()
    return g


def batch_in_phase_2(g, tmp_path, count, translator):
    videos = []
    for i in range(count):
        t = Task({"source": f"v{i}.mp4", "batch": "L"})
        t.state, t.prepared = "paused", SimpleNamespace(cues=[1] * 100, code="en")
        g.tasks[t.id] = t
        g.order.append(t.id)
        videos.append({"path": f"v{i}.mp4", "name": f"v{i}.mp4", "state": "prepared", "task": t.id, "count": 100, "error": ""})
    batch = Batch("r", videos, {"translator": translator}, tmp_path / "batch.json", "L")
    batch.phase = 2
    g.batches["L"] = batch
    return batch


def test_number_of_concurrent_videos_depends_on_the_translator():
    assert translation.concurrency(CLAUDE) == 5
    assert translation.concurrency(LOCAL) == 1
    assert translation.concurrency({**CLAUDE, "parallel": 3}) == 3
    assert translation.concurrency({**LOCAL, "parallel": "abc"}) == 1
    assert translation.concurrency({**CLAUDE, "parallel": 500}) == translation.MAX_CONCURRENCY


def test_claude_starts_five_videos_and_no_more(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "claude")
    batch = batch_in_phase_2(g, tmp_path, 8, "claude")
    g._feed_batches()
    g._feed_batches()                         # a second pass starts nothing more
    assert sum(v["state"] == "running" for v in batch.videos) == 5
    assert sum(g.tasks[v["task"]].state == "queued" for v in batch.videos) == 5


def test_the_parallel_setting_of_the_translator_is_respected(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "two")
    batch = batch_in_phase_2(g, tmp_path, 8, "two")
    g._feed_batches()
    assert sum(v["state"] == "running" for v in batch.videos) == 2


def test_local_ai_stays_at_one_video(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "local")
    batch = batch_in_phase_2(g, tmp_path, 8, "local")
    g._feed_batches()
    assert sum(v["state"] == "running" for v in batch.videos) == 1


def test_transcription_stays_one_at_a_time(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "claude")
    batch = batch_in_phase_2(g, tmp_path, 8, "claude")
    batch.phase = 1
    for v in batch.videos:
        v["state"], v["task"] = "todo", None
    g._feed_batches()
    assert sum(v["state"] == "running" for v in batch.videos) == 1


def test_a_freed_slot_starts_the_next_one(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "claude")
    batch = batch_in_phase_2(g, tmp_path, 8, "claude")
    g._feed_batches()
    first = batch.current()
    t = g.tasks[first["task"]]
    t.state = "done"
    g._update_batch(t)
    assert first["state"] == "done"
    g._feed_batches()
    assert sum(v["state"] == "running" for v in batch.videos) == 5
    assert sum(v["state"] == "prepared" for v in batch.videos) == 2


def test_the_phase_does_not_end_while_a_video_runs(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "claude")
    batch = batch_in_phase_2(g, tmp_path, 2, "claude")
    g._feed_batches()
    a, b = batch.videos
    t = g.tasks[a["task"]]
    t.state = "done"
    g._update_batch(t)
    g._feed_batches()
    assert batch.state == "running"
    t = g.tasks[b["task"]]
    t.state = "done"
    g._update_batch(t)
    g._feed_batches()
    assert batch.state == "done"


def test_suspended_batch_starts_nothing_more(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "claude")
    batch = batch_in_phase_2(g, tmp_path, 8, "claude")
    g._feed_batches()
    g.batch_suspend("L")
    t = g.tasks[batch.current()["task"]]
    t.state = "done"
    g._update_batch(t)
    g._feed_batches()
    assert sum(v["state"] == "running" for v in batch.videos) == 4


def test_parallel_translations_run_in_their_own_thread(monkeypatch, tmp_path):
    g = manager(monkeypatch, tmp_path, "claude")
    batch = batch_in_phase_2(g, tmp_path, 3, "claude")
    g._feed_batches()
    together = threading.Barrier(3, timeout=5)     # fails if the three do not run at the same time
    finished = []

    def run(t):
        together.wait()
        finished.append(t.id)

    monkeypatch.setattr(g, "_run", run)
    threads_before = set(threading.enumerate())
    for v in batch.videos:
        g._start(g.tasks[v["task"]])
    for f in set(threading.enumerate()) - threads_before:
        f.join(6)
    assert len(finished) == 3


def test_time_left_divided_by_the_number_of_concurrent_videos(tmp_path):
    videos = [{"path": f"v{i}", "name": f"v{i}", "state": "prepared", "task": None, "count": 100, "error": ""} for i in range(10)]
    batch = Batch("r", videos, {"languages": ["fr"]}, tmp_path / "batch.json", "L")
    batch.phase = 2
    alone = batch.remaining_s(known_rate=0.2)
    batch.concurrency = 5
    assert alone == 200
    assert batch.remaining_s(known_rate=0.2) == 40
