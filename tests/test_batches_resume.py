import json
import threading
from pathlib import Path

from subtitles import config, pipeline
from subtitles.web import tasks
from subtitles.web.batches import Batch


def manager(tmp_path, monkeypatch):
    """Manager without its execution thread or the real history: nothing really starts."""
    monkeypatch.setattr(tasks, "LAST_BATCH_FILE", tmp_path / "last_batch.json")
    g = tasks.Manager.__new__(tasks.Manager)
    g.tasks, g.order, g.batches, g.history = {}, [], {}, []
    g.lock, g.wake = threading.Lock(), threading.Event()
    return g


def a_batch(g, tmp_path, state="suspended"):
    videos = [{"path": "D:/a.mp4", "name": "a.mp4", "language": "en", "state": "done", "task": "t1", "count": 1, "detected_language": "en", "error": ""},
              {"path": "D:/b.mp4", "name": "b.mp4", "language": "", "state": "todo", "task": None, "count": None, "detected_language": "", "error": ""}]
    batch = Batch("D:/", videos, {"translator": "local", "languages": ["fr"], "pause": False}, tmp_path / "subtitles.batch.json")
    batch.state = state
    batch.after_save = g._remember_last_batch
    g.batches[batch.id] = batch
    batch.save()
    return batch


def test_resume_applies_the_new_translator_to_the_next_videos(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path)
    assert g.batch_resume(batch.id, {"translator": "claude", "languages": ["fr", "de"], "pause": True})
    g._feed_batches()
    t = g.tasks[batch.videos[1]["task"]]
    assert t.options["translator"] == "claude" and t.options["languages"] == ["fr", "de"] and t.options["pause"] is True


def test_resume_without_changes_keeps_the_settings(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path)
    assert g.batch_resume(batch.id, {})
    assert batch.options == {"translator": "local", "languages": ["fr"], "pause": False}


def test_last_batch_offered_at_the_next_session(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path)
    assert g.last_batch() is None                     # already open in this session
    following = manager(tmp_path, monkeypatch)
    d = following.last_batch()
    assert d["state"] == "suspended" and d["total"] == 2 and d["done"] == 1 and d["translator"] == "local"
    assert Path(d["file"]) == batch.file
    following.forget_last_batch()
    assert following.last_batch() is None and batch.file.exists()


def test_finished_batch_or_missing_file_not_offered(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path)
    batch.state = "done"
    batch.save()
    assert manager(tmp_path, monkeypatch).last_batch() is None

    batch2 = a_batch(g, tmp_path)
    batch2.file.unlink()
    assert manager(tmp_path, monkeypatch).last_batch() is None


def prepared(work, count=10):
    work.mkdir(parents=True, exist_ok=True)
    return pipeline.Prepared(work / "v.mp4", work, "v", "en", [object()] * count, work / "v.en.srt", work, 0.0)


def test_remaining_languages(tmp_path):
    p = prepared(tmp_path)
    assert pipeline.remaining_languages(p, ["en", "fr", "de"]) == ["fr", "de"]
    (tmp_path / "translation.de.json").write_text(json.dumps(["x"] * 10), encoding="utf-8")
    (tmp_path / "translation.fr.json").write_text(json.dumps(["x"] * 3), encoding="utf-8")   # other segmentation: to redo
    assert pipeline.remaining_languages(p, ["fr", "de"]) == ["fr"]
    assert pipeline.remaining_languages(p, ["fr", "de"], redo=True) == ["fr", "de"]


def test_cumulated_estimate_of_the_batch_without_the_translations_already_done(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "load", lambda: {"translation": {"chunk_size": 25}, "translators": [{"id": "claude", "type": "claude", "model": "claude-sonnet-5-5"}]})
    batch = a_batch(g, tmp_path, state="awaiting_translation")
    for i, v in enumerate(batch.videos):
        t = tasks.Task({"source": v["path"], "batch": batch.id})
        t.prepared = prepared(tmp_path / f"v{i}")
        g.tasks[t.id] = t
        v.update(state="prepared", task=t.id)
    e = g.estimate_batch(batch.id, ["fr"])
    assert e["videos"] == 2 and e["count"] == 20 and e["requests"] == 2 and e["input_tokens"] == 20 * 36
    (tmp_path / "v0" / "translation.fr.json").write_text(json.dumps(["x"] * 10), encoding="utf-8")
    e = g.estimate_batch(batch.id, ["fr"])
    assert e["videos"] == 1 and e["count"] == 10
    (tmp_path / "v1" / "translation.fr.json").write_text(json.dumps(["x"] * 10), encoding="utf-8")
    assert g.estimate_batch(batch.id, ["fr"])["languages"] == []


def test_last_video_translated_alone_ends_the_batch(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path, state="awaiting_translation")
    t = tasks.Task({"source": "D:/b.mp4", "batch": batch.id})
    t.state = "done"
    batch.videos[1].update(state="prepared", task=t.id)
    g._update_batch(t)
    assert batch.state == "done" and batch.videos[1]["state"] == "done"
    assert manager(tmp_path, monkeypatch).last_batch() is None


def test_fully_done_batch_not_offered_even_if_its_state_stayed_open(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path, state="awaiting_translation")
    batch.videos[1]["state"] = "done"
    batch.save()
    assert manager(tmp_path, monkeypatch).last_batch() is None


def test_loading_a_batch_already_open_does_not_block(tmp_path, monkeypatch):
    g = manager(tmp_path, monkeypatch)
    batch = a_batch(g, tmp_path, state="suspended")
    result = []
    thread = threading.Thread(target=lambda: result.append(g.load_batch(str(batch.file))), daemon=True)
    thread.start()
    thread.join(5)
    assert result and result[0].state == "suspended"
    assert manager(tmp_path, monkeypatch).last_batch() is not None     # the reloaded batch stays offered
