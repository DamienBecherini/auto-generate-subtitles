"""Resuming a batch: the videos already transcribed go first, and do not count in the time left."""
import threading

from subtitles import config, pipeline
from subtitles.web import tasks
from subtitles.web.batches import Batch


def video(name, state="todo", **others):
    return {"path": f"D:/{name}.mp4", "name": f"{name}.mp4", "language": "", "state": state, "task": None, "count": None,
            "detected_language": "", "error": "", "duration": 100, **others}


def batch_of(tmp_path, videos, options=None, phase=1):
    batch = Batch("D:/", videos, options or {}, tmp_path / "subtitles.batch.json")
    batch.phase = phase
    return batch


def test_videos_already_transcribed_go_first(tmp_path):
    batch = batch_of(tmp_path, [video("a"), video("b", transcribed=True), video("c", transcribed=True)])
    assert batch.next_video() is batch.videos[1]
    batch.videos[1]["state"] = "prepared"
    assert batch.next_video() is batch.videos[2]
    batch.videos[2]["state"] = "prepared"
    assert batch.next_video() is batch.videos[0]


def test_without_kept_transcription_the_folder_order_is_kept(tmp_path):
    batch = batch_of(tmp_path, [video("a"), video("b")])
    assert batch.next_video() is batch.videos[0]


def test_in_phase_2_the_ready_videos_stay_in_order(tmp_path):
    batch = batch_of(tmp_path, [video("a", "prepared"), video("b", "prepared", transcribed=True)], phase=2)
    assert batch.next_video() is batch.videos[0]


def test_finished_batch_nothing_more_to_start(tmp_path):
    assert batch_of(tmp_path, [video("a", "prepared")]).next_video() is None


def test_time_left_ignores_the_kept_transcriptions(tmp_path):
    batch = batch_of(tmp_path, [video("a"), video("b", transcribed=True), video("c", transcribed=True)])
    assert batch.remaining_s(known_rate=2.0) == 200


def test_detection_from_the_working_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path)})
    videos = [video("a"), video("b")]
    work = pipeline.work_dir(tmp_path, videos[1]["path"])
    work.mkdir(parents=True)
    (work / "transcription.json").write_text("{}", encoding="utf-8")
    batch = batch_of(tmp_path, videos)
    tasks.Manager._mark_transcribed(batch)
    assert [v["transcribed"] for v in batch.videos] == [False, True]


def test_redo_transcribes_everything_again(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path)})
    videos = [video("a")]
    work = pipeline.work_dir(tmp_path, videos[0]["path"])
    work.mkdir(parents=True)
    (work / "transcription.json").write_text("{}", encoding="utf-8")
    batch = batch_of(tmp_path, videos, {"redo": True})
    tasks.Manager._mark_transcribed(batch)
    assert batch.videos[0]["transcribed"] is False


def test_resume_detects_the_transcriptions(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "LAST_BATCH_FILE", tmp_path / "last_batch.json")
    monkeypatch.setattr(config, "load", lambda: {"output_dir": str(tmp_path)})
    g = tasks.Manager.__new__(tasks.Manager)
    g.tasks, g.order, g.batches, g.history = {}, [], {}, []
    g.lock, g.wake = threading.Lock(), threading.Event()
    monkeypatch.setattr(g, "_measure_durations", lambda batch: None)
    videos = [video("a"), video("b")]
    work = pipeline.work_dir(tmp_path, videos[1]["path"])
    work.mkdir(parents=True)
    (work / "transcription.json").write_text("{}", encoding="utf-8")
    file = tmp_path / "subtitles.batch.json"
    batch_of(tmp_path, videos).save()
    resumed = g.load_batch(str(file))
    assert resumed.next_video() is resumed.videos[1]
