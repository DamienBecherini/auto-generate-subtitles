import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from subtitles import config
from subtitles.web import batch_manager, timing
from subtitles.web.batches import Batch
from subtitles.web.manager import Manager
from subtitles.web.tasks import Task, TaskQueue


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(timing, "FILE", tmp_path / "speeds.json")
    monkeypatch.setattr(timing, "_memory", None)


def batch_of(states, duration=600, phase=1, options=None):
    videos = [{"path": f"/v{i}.mp4", "state": e, "duration": duration, "count": 100} for i, e in enumerate(states)]
    batch = Batch("/r", videos, options or {}, Path("/r/f.json"))
    batch.phase = phase
    return batch


def test_nothing_to_estimate_without_measure_or_speed():
    assert batch_of(["running", "todo"]).remaining_s() is None


def test_stopped_batch_has_no_time_left():
    batch = batch_of(["todo"])
    batch.state = "suspended"
    assert batch.remaining_s(known_rate=0.1) is None


def test_known_speed_before_the_first_measure():
    batch = batch_of(["todo", "todo"])
    assert batch.remaining_s(known_rate=0.1) == 120


def test_the_observed_speed_wins_over_the_known_speed():
    batch = batch_of(["done", "todo"])
    batch.record_pass(600, 300)
    assert batch.remaining_s(known_rate=0.1) == 300


def test_the_time_already_spent_on_the_current_video_is_deducted():
    batch = batch_of(["running", "todo"])
    batch.videos[0]["started"] = 1000
    assert batch.remaining_s(known_rate=0.1, now=1040) == 20 + 60


def test_a_video_that_overruns_does_not_make_the_time_negative():
    batch = batch_of(["running"])
    batch.videos[0]["started"] = 0
    assert batch.remaining_s(known_rate=0.1, now=1e6) == 0


def test_a_longer_video_counts_more():
    batch = batch_of(["todo", "todo"])
    batch.videos[0]["duration"], batch.videos[1]["duration"] = 600, 1800
    assert batch.remaining_s(known_rate=0.1) == 240


def test_unknown_duration_replaced_by_the_mean():
    batch = batch_of(["todo", "todo"])
    batch.videos[1]["duration"] = 0
    assert batch.remaining_s(known_rate=0.1) == 120


def test_translation_counted_in_lines_and_languages():
    batch = batch_of(["prepared"], phase=2, options={"languages": ["fr", "de"]})
    assert batch.remaining_s(known_rate=0.5) == 100


def test_distinct_keys_depending_on_what_works():
    o = {"transcription": "t", "translator": "c", "pause": True}
    assert timing.key(1, o) == "transcription|t"
    assert timing.key(1, {**o, "pause": False}) == "full|t|c"
    assert timing.key(2, o) == "translation|c"


def test_speed_remembered_from_one_session_to_the_next(monkeypatch):
    timing.record("x", 0.2)
    timing.record("x", 0.4)
    monkeypatch.setattr(timing, "_memory", None)
    assert timing.read("x") == pytest.approx(0.3)


def cfg(translator_type="openai"):
    return {"active_translator": "t", "active_transcription": "s", "estimation": {"local_seconds_per_line": 0.5},
            "translators": [{"id": "t", "type": translator_type}], "transcriptions": [{"id": "s", "speed": 10}]}


def test_announced_speed_of_the_server(monkeypatch):
    monkeypatch.setattr(config, "load", cfg)
    assert timing.known_rate(1, {"pause": True}) == pytest.approx(0.1)
    assert timing.known_rate(2, {}) == pytest.approx(0.5)


def test_without_pause_the_translation_is_counted(monkeypatch):
    monkeypatch.setattr(config, "load", cfg)
    assert timing.known_rate(1, {"pause": False, "languages": ["fr"]}) == pytest.approx(0.1 + timing.LINES_PER_SECOND * 0.5)


def test_claude_translator_without_announced_duration(monkeypatch):
    monkeypatch.setattr(config, "load", lambda: cfg("claude"))
    assert timing.known_rate(2, {}) is None


def running_task(duration=600, **options):
    t = Task({"source": "/v.mp4", "video_duration": duration, **options})
    t.state, t.pass_started = "running", 1000.0
    return t


def test_single_task(monkeypatch):
    monkeypatch.setattr(timing, "known_rate", lambda *_: 0.1)
    assert running_task().remaining_s(now=1020) == 40


def test_task_without_duration_or_active_state(monkeypatch):
    monkeypatch.setattr(timing, "known_rate", lambda *_: 0.1)
    assert running_task(duration=0).remaining_s() is None
    t = running_task()
    t.state = "paused"
    assert t.remaining_s() is None


def test_task_in_translation_counted_in_lines(monkeypatch):
    monkeypatch.setattr(timing, "known_rate", lambda *_: 0.5)
    t = running_task(languages=["fr", "de"])
    t.pass_no, t.prepared = 2, SimpleNamespace(cues=[0] * 100)
    assert t.remaining_s(now=1000) == 100


def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(batch_manager, "LAST_BATCH_FILE", tmp_path / "last_batch.json")
    g = Manager(TaskQueue(history=[]), start=False)
    return g


def test_end_of_pass_remembers_the_speed(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = g.batches.all["L"] = batch_of(["running"])
    t = running_task(batch="L", transcription="s", pause=True)
    t.pass_started = time.time() - 60
    g.queue._end_of_pass(t)
    assert timing.read("transcription|s") == pytest.approx(0.1, abs=0.01)
    assert len(batch.passes[1]) == 1


def test_pass_resumed_in_a_few_seconds_ignored(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    t = running_task(transcription="s", pause=True)
    t.pass_started = time.time() - 1
    g.queue._end_of_pass(t)
    assert timing.read("transcription|s") is None
