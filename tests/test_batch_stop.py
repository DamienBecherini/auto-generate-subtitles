""""Stop" a batch: paused at once, the running videos interrupted and put back in the list, nothing lost."""
from subtitles import config
from subtitles.states import BatchState, TaskState, VideoState
from subtitles.web.batches import Batch
from subtitles.web.manager import Manager
from subtitles.web.tasks import TaskQueue


def setup(tmp_path, monkeypatch, phase=1, task_state=TaskState.RUNNING, pass_no=1):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path), "translators": [], "active_translator": ""})
    g = Manager(TaskQueue(history=[]), start=False)
    videos = [{"path": f"/v{i}.mp4", "name": f"v{i}.mp4", "language": "", "state": VideoState.TODO, "task": None, "error": ""} for i in range(3)]
    batch = Batch("/r", videos, {"pause": False}, tmp_path / "subtitles.batch.json", "L")
    batch.phase = phase
    g.batches.all["L"] = batch
    t = g.queue.add({"source": "/v0.mp4", "batch": "L"})
    t.state, t.pass_no = task_state, pass_no
    videos[0].update(state=VideoState.RUNNING, task=t.id)
    videos[1]["state"] = VideoState.DONE
    return g, batch, t


def test_stop_pauses_and_interrupts_the_running_video(tmp_path, monkeypatch):
    g, batch, t = setup(tmp_path, monkeypatch)
    assert g.batches.stop("L")
    assert batch.state == BatchState.SUSPENDED and t.cancel_requested
    t.state = TaskState.CANCELLED                 # what the task does at the end of its current chunk
    g.batches.task_changed(t)
    v = batch.videos[0]
    assert v["state"] == VideoState.TODO and v["task"] is None and v["error"] == ""
    assert batch.videos[1]["state"] == VideoState.DONE and batch.state == BatchState.SUSPENDED


def test_a_video_stopped_while_dubbing_waits_for_the_dubbing_again(tmp_path, monkeypatch):
    g, batch, t = setup(tmp_path, monkeypatch, phase=3, pass_no=3)
    g.batches.stop("L")
    t.state = TaskState.CANCELLED
    g.batches.task_changed(t)
    assert batch.videos[0]["state"] == VideoState.TRANSLATED


def test_a_waiting_task_stops_at_once(tmp_path, monkeypatch):
    g, batch, t = setup(tmp_path, monkeypatch, task_state=TaskState.QUEUED)
    g.batches.stop("L")
    assert t.state == TaskState.CANCELLED and batch.videos[0]["state"] == VideoState.TODO


def test_resume_starts_the_stopped_video_again(tmp_path, monkeypatch):
    g, batch, t = setup(tmp_path, monkeypatch, task_state=TaskState.QUEUED)
    g.batches.stop("L")
    assert g.batches.resume("L")
    g.batches.feed()
    v = batch.videos[0]
    assert v["state"] == VideoState.RUNNING and v["task"] != t.id


def test_cancel_still_cancels(tmp_path, monkeypatch):
    g, batch, t = setup(tmp_path, monkeypatch)
    g.batches.cancel("L")
    t.state = TaskState.CANCELLED
    g.batches.task_changed(t)
    assert batch.videos[0]["state"] == VideoState.CANCELLED and batch.state == BatchState.CANCELLED


def test_only_a_running_batch_can_be_stopped(tmp_path, monkeypatch):
    g, batch, _t = setup(tmp_path, monkeypatch)
    batch.state = BatchState.SUSPENDED
    assert not g.batches.stop("L") and not g.batches.stop("unknown")


def test_a_translation_stopped_on_request_is_cancelled_not_in_error(tmp_path, monkeypatch):
    """The translator stops with its own error when asked to: the task is then cancelled, not in error."""
    from subtitles import pipeline, translation
    q = TaskQueue(history=[])
    t = q.add({"source": str(tmp_path / "v.mp4")})
    from types import SimpleNamespace
    t.prepared = SimpleNamespace(video=tmp_path / "v.mp4", code="en", cues=[1], source_srt=tmp_path / "v.en.srt", work=tmp_path)
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path)})

    def interrupted(*_a, **_k):
        t.cancel_requested = True
        raise translation.TranslationError("annulé")
    monkeypatch.setattr(pipeline, "translate_and_copy", interrupted)
    monkeypatch.setattr(q, "request", lambda *_a: pipeline.Request("v.mp4", {}))
    q._run(t)
    assert t.state == TaskState.CANCELLED and t.error == ""
