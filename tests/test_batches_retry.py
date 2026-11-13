""""Retry" a video in error of a batch, whatever the state of the batch."""

from subtitles.web import batch_manager
from subtitles.web.batches import Batch
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue


def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(batch_manager, "LAST_BATCH_FILE", tmp_path / "last_batch.json")
    g = Manager(TaskQueue(history=[]), start=False)
    return g


def a_batch(g, tmp_path, states, state="running", phase=1, options=None):
    videos = [{"path": f"/v{i}.mp4", "name": f"v{i}.mp4", "language": "", "state": e, "task": f"t{i}", "count": 10, "detected_language": "",
               "error": "Pas de réponse" if e == "error" else ""} for i, e in enumerate(states)]
    batch = Batch("/r", videos, options or {"pause": True}, tmp_path / "subtitles.batch.json")
    batch.state, batch.phase = state, phase
    g.batches.all[batch.id] = batch
    return batch


def test_the_video_starts_from_scratch(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path, ["prepared", "error", "todo"])
    assert g.batches.retry(batch.id, "/v1.mp4")
    v = batch.videos[1]
    assert (v["state"], v["task"], v["error"]) == ("todo", None, "")
    assert g.queue.wake.is_set()


def test_a_single_video_targeted(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path, ["error", "error"])
    g.batches.retry(batch.id, "/v0.mp4")
    assert [v["state"] for v in batch.videos] == ["todo", "error"]


def test_every_video_in_error(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path, ["error", "prepared", "error"])
    g.batches.retry(batch.id)
    assert [v["state"] for v in batch.videos] == ["todo", "prepared", "todo"]


def test_suspended_batch_stays_suspended(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path, ["error"], state="suspended")
    assert g.batches.retry(batch.id)
    assert batch.state == "suspended"


def test_finished_batch_starts_again(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path, ["done", "error"], state="done", phase=2)
    batch.finished = 123.0
    assert g.batches.retry(batch.id)
    assert (batch.state, batch.finished) == ("running", None)


def test_batch_awaiting_translation_finishes_this_video_first(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path, ["prepared", "error"], state="awaiting_translation")
    assert g.batches.retry(batch.id)
    assert (batch.state, batch.phase) == ("running", 1)
    assert batch.next_video() is batch.videos[1]


def test_refused_without_video_in_error_or_cancelled_batch(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    assert not g.batches.retry(a_batch(g, tmp_path, ["prepared"]).id)
    assert not g.batches.retry(a_batch(g, tmp_path, ["error"], state="cancelled").id)
    assert not g.batches.retry("unknown")


def test_phase_1_starts_again_with_the_batch_options(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path, ["error"])
    g.batches.retry(batch.id)
    g.batches.feed()
    t = next(iter(g.queue.tasks.values()))
    assert t.options["source"] == "/v0.mp4" and t.options["pause"] is True
    assert (batch.videos[0]["state"], batch.videos[0]["task"]) == ("running", t.id)


def test_phase_2_redoes_the_whole_video_without_pause(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path, ["done", "error"], phase=2)
    g.batches.retry(batch.id)
    g.batches.feed()
    t = next(iter(g.queue.tasks.values()))
    assert t.options["source"] == "/v1.mp4" and t.options["pause"] is False


def test_phase_2_translates_the_ready_videos_first(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path, ["todo", "prepared"], phase=2)
    assert batch.next_video() is batch.videos[1]
